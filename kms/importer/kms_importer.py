from __future__ import annotations
import bpy
from ..kms import *
import os
from mathutils import Vector
from ...util.util import getBoneName, expected_parent_bones, setRawNormalAttribute, setRawPositionAttribute, captureNormalState
from ...util.materials import TextureLoad
from ...util.packet import uvMask
from .rotationWrapperObj import objRotationWrapper
import bmesh
from ...cmdl.exporter.cmdl_cooker import strip_triangles

DEFAULT_BONE_LENGTH = 100

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
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = parent
    child.select_set(True)
    parent.select_set(True)
    bpy.ops.object.parent_set(type="ARMATURE")
    child.select_set(False)
    parent.select_set(False)


def construct_mesh(mesh: KMSMesh, kmsCollection, meshInd: int, meshPos, extract_dir: str, hasHumanBones: bool, texLoader: TextureLoad):
    print(f"Importing mesh {meshInd}, parent {mesh.parentInd}, pos {meshPos}")
    vertices = []
    normals = []
    faces = []
    materialIndices = []
    uvs = []
    uvs2 = []
    uvs3 = []
    weights = []
    for i, vertexGroup in enumerate(mesh.vertexGroups):
        faceIndexOffset = len(vertices)
        # keep raw vertex positions; the stored bounds aren't a safe range to clamp them to.
        vertices += [(vert.x, vert.y, vert.z) for vert in vertexGroup.vertices]
        normals += [(-nrm.x / 4096, -nrm.y / 4096, -nrm.z / 4096) for nrm in vertexGroup.normals]
        weights += [vert.weight for vert in vertexGroup.vertices]
        if vertexGroup.uvs:
            uvs += [(uv.u / 4096, 1 - uv.v / 4096) for uv in vertexGroup.uvs]
        else:
            uvs += [(0, 0) for _ in range(len(vertexGroup.vertices))]
        if vertexGroup.uvs2:
            uvs2 += [(uv.u / 4096, 1 - uv.v / 4096) for uv in vertexGroup.uvs2]
        else:
            uvs2 += [(0, 0) for _ in range(len(vertexGroup.vertices))]
        if vertexGroup.uvs3:
            uvs3 += [(uv.u / 4096, 1 - uv.v / 4096) for uv in vertexGroup.uvs3]
        else:
            uvs3 += [(0, 0) for _ in range(len(vertexGroup.vertices))]
        for a, b, c in strip_triangles(vertexGroup):
            faces.append((a + faceIndexOffset, c + faceIndexOffset, b + faceIndexOffset))
            materialIndices.append(i)

    
    objmesh = bpy.data.meshes.new("kmsMesh%d" % meshInd)
    obj = bpy.data.objects.new(objmesh.name, objmesh)
    obj.location = Vector(meshPos)
    obj['flag'] = mesh.flag
    obj['sealouse_mesh_index'] = meshInd
    kmsCollection.objects.link(obj)
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
    boneName = getBoneName(meshInd) if hasHumanBones else f"bone{meshInd}"
    obj.vertex_groups.new(name=boneName)
    group = obj.vertex_groups[boneName]
    for i, x in enumerate(weights):
        group.add([i], x / 4096, "REPLACE")
    if mesh.parent: # 2 bones
        parentBoneName = getBoneName(mesh.parentInd) if hasHumanBones else f"bone{mesh.parentInd}"
        parentGroup = obj.vertex_groups.new(name=parentBoneName)
        for i, x in enumerate(weights):
            parentGroup.add([i], 1 - x / 4096, "REPLACE")
    
    if apply_materials(mesh, obj, extract_dir, texLoader):
        bm = bmesh.new()
        bm.from_mesh(objmesh)
        uv_layer = bm.loops.layers.uv.new("UVMap1")
        for i, face in enumerate(bm.faces):
            face.material_index = materialIndices[i]
            for l in face.loops:
                ind = l.vert.index
                l[uv_layer].uv = Vector(uvs[ind])
        if any(p.uvs2 is not None or p.uvs3 is not None for p in mesh.vertexGroups):
            uv_layer2 = bm.loops.layers.uv.new("UVMap2")
            for i, face in enumerate(bm.faces):
                for l in face.loops:
                    ind = l.vert.index
                    l[uv_layer2].uv = Vector(uvs2[ind])
        if any(p.uvs3 is not None for p in mesh.vertexGroups):
            uv_layer3 = bm.loops.layers.uv.new("UVMap3")
            for i, face in enumerate(bm.faces):
                for l in face.loops:
                    ind = l.vert.index
                    l[uv_layer3].uv = Vector(uvs3[ind])
            
        bm.to_mesh(objmesh)
        bm.free()

    captureNormalState(objmesh)
    
    return obj

def construct_armature(kms: KMS, kmsName: str, hasHumanBones: bool):
    print("Creating armature")
    amt = bpy.data.armatures.new(kmsName +'Amt')
    ob = bpy.data.objects.new(kmsName, amt)
    ob.name = kmsName
    bpy.data.collections.get(kmsName).objects.link(ob)
    
    ob["bboxMin"] = kms.header.minPos.xyz()
    ob["bboxMax"] = kms.header.maxPos.xyz()
    ob["kmsType"] = kms.header.kmsType
    ob["strcode"] = kms.header.strcode
    ob.location = tuple(kms.header.pos.xyz())
    
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    
    for i, mesh in enumerate(kms.meshes):
        meshPos = mesh.pos
        curMesh = mesh
        while curMesh.parent:
            curMesh = curMesh.parent
            meshPos += curMesh.pos
        bone = amt.edit_bones.new(getBoneName(i) if hasHumanBones else f"bone{i}")
        bone['sealouse_bone_index'] = i
        bone['sealouse_runtime_bone'] = i < kms.header.numBones
        bone.head = Vector(tuple(meshPos.xyz()))
        bone.tail = bone.head + Vector((0, DEFAULT_BONE_LENGTH, 0))
    
    # Parenting
    bones = amt.edit_bones
    for i, bone in enumerate(bones):
        mesh = kms.meshes[i]
        if mesh.parent is None:
            continue
        bone.parent = bones[mesh.parentInd]
        # aim the default parent tail at its child.
        if bone.parent.tail == bone.parent.head + Vector((0, DEFAULT_BONE_LENGTH, 0)):
            bone.parent.tail = bone.head
            dist = bone.parent.head - bone.parent.tail
            if abs(dist.x) + abs(dist.y) + abs(dist.z) < 10:
                bone.parent.tail += Vector((0, DEFAULT_BONE_LENGTH, 0))
    
    bpy.ops.object.mode_set(mode='OBJECT')
    return ob

def apply_materials(mesh: KMSMesh, obj, extract_dir: str, texLoader: TextureLoad):
    if mesh.numVertexGroup == 0:
        return False
    
    for vGroup in mesh.vertexGroups:
        material = texLoader.makeMaterial(obj.name, vGroup.flag, vGroup.colorMap, vGroup.specularMap, vGroup.environmentMap)
        material['sealouse_uv_mask'] = uvMask(vGroup)
        
        obj.data.materials.append(material)
    return True

def main(kms_file: str, ctxr_path: str = None, overwrite_existing: bool = False, tri_dir: str = None):
    kms = KMS()
    with open(kms_file, "rb") as f:
        kms.fromFile(f)

    return import_model(kms, kms_file, ctxr_path, overwrite_existing, tri_dir)


def import_model(kms, kms_file, ctxr_path=None, overwrite_existing=False, tri_dir=None):

    if not kms.hasLocalHierarchy:
        print("KMS parent links do not form a local skeleton; importing independent parts and preserving attachment indices.")
    
    
    extract_dir, kmsname = os.path.split(kms_file)
    extract_dir = os.path.join(extract_dir, "sealouse_extract")

    kmsCollection = bpy.data.collections.get("KMS")
    if not kmsCollection:
        kmsCollection = bpy.data.collections.new("KMS")
        bpy.context.scene.collection.children.link(kmsCollection)

    collection_name = os.path.splitext(kmsname)[0]
    if bpy.data.collections.get(collection_name): # oops, duplicate
        collection_suffix = 1
        while bpy.data.collections.get(f"{collection_name}.{collection_suffix:03}"):
            collection_suffix += 1
        collection_name += f".{collection_suffix:03}"
    col = bpy.data.collections.new(collection_name)
    
    kmsCollection.children.link(col)
    
    parentBoneList = [mesh.parentInd for mesh in kms.meshes]
    hasHumanBones = parentBoneList[:len(expected_parent_bones)] == expected_parent_bones
    
    texLoader = TextureLoad(extract_dir, ctxr_path, overwrite_existing, tri_dir, kms.header.strcode)
    
    bMeshes = []
    for i, mesh in enumerate(kms.meshes):
        meshPos = kms.header.pos
        # meshes beyond numBones use only the model and ancestor offsets.
        if i < kms.header.numBones:
            meshPos += mesh.pos
        curMesh = mesh
        while curMesh.parent:
            curMesh = curMesh.parent
            meshPos += curMesh.pos
        bMeshes.append(construct_mesh(mesh,
                                      col,
                                      i,
                                      tuple(meshPos.xyz()),
                                      extract_dir,
                                      hasHumanBones,
                                      texLoader))
        if not kms.hasLocalHierarchy:
            bMeshes[-1]['kms_attachment_parent'] = mesh.parentInd
            if mesh.parentInd != -1:
                group = bMeshes[-1].vertex_groups.new(name=f"attachment{mesh.parentInd}")
                for vertex in bMeshes[-1].data.vertices:
                    weight = bMeshes[-1].vertex_groups[0].weight(vertex.index)
                    group.add([vertex.index], 1 - weight, 'REPLACE')
    
    amt = construct_armature(kms, collection_name, hasHumanBones)
    for mesh in bMeshes:
        set_partent(amt, mesh)
    
    objRotationWrapper(amt)
    from ...util.import_state import capture

    capture(col, kms)
    
    print('Importing finished. ;)')
    return {'FINISHED'}
