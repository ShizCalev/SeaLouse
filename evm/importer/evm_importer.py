import bpy
from ..evm import *
import os
from mathutils import Vector
from ...kms.importer.rotationWrapperObj import objRotationWrapper
from ...util.util import getBoneName, expected_parent_bones, setRawNormalAttribute, setRawPositionAttribute, captureNormalState
from ...util.materials import TextureLoad
from ...util.packet import uvMask
import bmesh
from ...cmdl.exporter.cmdl_cooker import strip_triangles

DEFAULT_BONE_LENGTH = 10

# Credit WoefulWolf/Nier2Blender2Nier
def reset_blend():
    #bpy.ops.object.mode_set(mode='OBJECT')
    for collection in bpy.data.collections:
        for obj in collection.objects:
            collection.objects.unlink(obj)
        bpy.data.collections.remove(collection)
    for bpy_data_iter in (bpy.data.objects, bpy.data.meshes, bpy.data.lights, bpy.data.cameras, bpy.data.libraries):
        for id_data in bpy_data_iter:
            bpy_data_iter.remove(id_data)
    for material in bpy.data.materials:
        bpy.data.materials.remove(material)
    for amt in bpy.data.armatures:
        bpy.data.armatures.remove(amt)
    for obj in bpy.data.objects:
        bpy.data.objects.remove(obj)
        obj.user_clear()

# Credit WoefulWolf/Nier2Blender2Nier
def set_partent(parent, child):
    bpy.context.view_layer.objects.active = parent
    child.select_set(True)
    parent.select_set(True)
    bpy.ops.object.parent_set(type="ARMATURE")
    child.select_set(False)
    parent.select_set(False)


def construct_mesh(evm: EVM, evmCollection, extract_dir: str, hasHumanBones: bool, texLoader: TextureLoad):
    print("Importing mesh")
    vertices = []
    normals = []
    faces = []
    materialIndices = []
    uvs = []
    uvs2 = []
    uvs3 = []
    weights = []
    boneIndices = []
    posScale = evmPosScale(evm.header.flag)
    for i, vertexGroup in enumerate(evm.meshes):
        faceIndexOffset = len(vertices)
        vertices += [(vert.x * posScale, vert.y * posScale, vert.z * posScale) for vert in vertexGroup.vertices]
        normals += [(-nrm.x / 4096, -nrm.y / 4096, -nrm.z / 4096) for nrm in vertexGroup.normals]
        if vertexGroup.uvs:
            uvs += [(uv.u / 4096, 1 - uv.v / 4096) for uv in vertexGroup.uvs]
        else:
            uvs += [(0, 1) for _ in range(vertexGroup.numVertex)]
        if vertexGroup.uvs2:
            uvs2 += [(uv.u / 4096, 1 - uv.v / 4096) for uv in vertexGroup.uvs2]
        else:
            uvs2 += [(0, 1) for _ in range(vertexGroup.numVertex)]
        if vertexGroup.uvs3:
            uvs3 += [(uv.u / 4096, 1 - uv.v / 4096) for uv in vertexGroup.uvs3]
        else:
            uvs3 += [(0, 1) for _ in range(vertexGroup.numVertex)]
        
        for a, b, c in strip_triangles(vertexGroup, evm=True):
            faces.append((a + faceIndexOffset, c + faceIndexOffset, b + faceIndexOffset))
            materialIndices.append(i)

    
    objmesh = bpy.data.meshes.new("evmMesh")
    obj = bpy.data.objects.new(objmesh.name, objmesh)
    obj.location = Vector((0,0,0))
    evmCollection.objects.link(obj)
    objmesh.from_pydata(vertices, [], faces, False)
    if bpy.app.version < (4, 1):
        objmesh.use_auto_smooth = True
    objmesh.normals_split_custom_set_from_vertices(normals)
    setRawNormalAttribute(objmesh, normals)
    setRawPositionAttribute(objmesh, vertices)
    if bpy.app.version < (4, 1):
        objmesh.calc_normals_split()
    objmesh.update(calc_edges=True)
    
    # Bone weights
    i = 0
    vgroups = obj.vertex_groups
    for vertexGroup in evm.meshes:
        if vertexGroup.weights is None:
            i += vertexGroup.numVertex
            continue
        
        for skinIndex in vertexGroup.skinningTable:
            if skinIndex == 0xff:
                continue
            skinName = getBoneName(skinIndex, evm.header.fingerIndex) if hasHumanBones else f"bone{skinIndex}"
            if not vgroups.get(skinName):
                vgroups.new(name=skinName)
        
        for weight_list in vertexGroup.weights:
            for j in range(vertexGroup.numSkin):
                weight = weight_list.weights[j]
                boneIndex = vertexGroup.skinningTable[weight_list.indices[j] >> 2]
                boneName = getBoneName(boneIndex, evm.header.fingerIndex) if hasHumanBones else f"bone{boneIndex}"
                vgroups[boneName].add([i], weight / 128, "ADD")
            i += 1
    
    if apply_materials(evm, obj, extract_dir, texLoader):
        bm = bmesh.new()
        bm.from_mesh(objmesh)
        uv_layer = bm.loops.layers.uv.new("UVMap1")
        for i, face in enumerate(bm.faces):
            face.material_index = materialIndices[i]
            for l in face.loops:
                ind = l.vert.index
                l[uv_layer].uv = Vector(uvs[ind])
        if any(p.uvs2 is not None or p.uvs3 is not None for p in evm.meshes):
            uv_layer2 = bm.loops.layers.uv.new("UVMap2")
            for i, face in enumerate(bm.faces):
                for l in face.loops:
                    ind = l.vert.index
                    l[uv_layer2].uv = Vector(uvs2[ind])
        if any(p.uvs3 is not None for p in evm.meshes):
            uv_layer3 = bm.loops.layers.uv.new("UVMap3")
            for i, face in enumerate(bm.faces):
                for l in face.loops:
                    ind = l.vert.index
                    l[uv_layer3].uv = Vector(uvs3[ind])
            
        bm.to_mesh(objmesh)
        bm.free()
    
    captureNormalState(objmesh)
    return obj

def construct_armature(evm: EVM, evmName: str, hasHumanBones: bool):
    print("Creating armature")
    amt = bpy.data.armatures.new(evmName +'Amt')
    ob = bpy.data.objects.new(evmName, amt)
    ob.name = evmName
    bpy.data.collections.get(evmName).objects.link(ob)
    
    ob["bboxMin"] = evm.header.minPos.xyz()
    ob["bboxMax"] = evm.header.maxPos.xyz()
    ob["strcode"] = evm.header.strcode
    ob["flag"] = evm.header.flag
    ob['fingerIndex'] = evm.header.fingerIndex
    
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    
    for i, evmBone in enumerate(evm.bones):
        boneName = getBoneName(i, evm.header.fingerIndex) if hasHumanBones else f"bone{i}"
        bone = amt.edit_bones.new(boneName)
        bone['sealouse_bone_index'] = i
        # blender integer properties are signed; preserve all 32 flag bits.
        bone['sealouse_bone_flag'] = evmBone.flag if evmBone.flag < 0x80000000 else evmBone.flag - 0x100000000
        bone.head = Vector(tuple(evmBone.worldPos.xyz()))
        bone.tail = bone.head + Vector((0, DEFAULT_BONE_LENGTH, 0))
    
    # Parenting
    bones = amt.edit_bones
    for i, evmBone in enumerate(evm.bones):
        if evmBone.parentInd == -1:
            continue
        bone = bones[i]
        bone.parent = bones[evmBone.parentInd]
        # aim the default parent tail at its child.
        if bone.parent.tail == bone.parent.head + Vector((0, DEFAULT_BONE_LENGTH, 0)):
            bone.parent.tail = bone.head
            dist = bone.parent.head - bone.parent.tail
            if abs(dist.x) + abs(dist.y) + abs(dist.z) < DEFAULT_BONE_LENGTH:
                bone.parent.tail += Vector((0, DEFAULT_BONE_LENGTH, 0))
    
    bpy.ops.object.mode_set(mode='OBJECT')
    return ob

def apply_materials(evm: EVM, obj, extract_dir: str, texLoader: TextureLoad):
    if evm.header.numMeshes == 0:
        return False
    
    for vGroup in evm.meshes:
        material = texLoader.makeMaterial(obj.name, vGroup.flag, vGroup.colorMap, vGroup.specularMap, vGroup.environmentMap)
        material['sealouse_uv_mask'] = uvMask(vGroup)
        
        obj.data.materials.append(material)

    return True

def main(evm_file: str, ctxr_path: str = None, overwrite_existing: bool = False, tri_dir: str = None):
    evm = EVM()
    with open(evm_file, "rb") as f:
        evm.fromFile(f)
    
    
    extract_dir, evmname = os.path.split(evm_file)
    extract_dir = os.path.join(extract_dir, "sealouse_extract")

    evmCollection = bpy.data.collections.get("EVM")
    if not evmCollection:
        evmCollection = bpy.data.collections.new("EVM")
        bpy.context.scene.collection.children.link(evmCollection)

    collection_name = evmname[:-4]
    if bpy.data.collections.get(collection_name): # oops, duplicate
        collection_suffix = 1
        while bpy.data.collections.get(f"{collection_name}.{collection_suffix:03}"):
            collection_suffix += 1
        collection_name += f".{collection_suffix:03}"
    col = bpy.data.collections.new(collection_name)
    
    evmCollection.children.link(col)
    
    parentBoneList = [bone.parentInd for bone in evm.bones]
    hasHumanBones = parentBoneList[:len(expected_parent_bones)] == expected_parent_bones
    
    texLoader = TextureLoad(extract_dir, ctxr_path, overwrite_existing, tri_dir, evm.header.strcode)
    
    mesh = construct_mesh(evm, col, extract_dir, hasHumanBones, texLoader)
    amt = construct_armature(evm, collection_name, hasHumanBones)
    set_partent(amt, mesh)
    
    objRotationWrapper(amt)
    from ...util.import_state import capture

    capture(col, evm)
    
    print('Importing finished. ;)')
    return {'FINISHED'}
