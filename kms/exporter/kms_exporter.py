import bpy
from ...util.atomic_output import atomic_output
from ..kms import *
from ...util.util import getVertWeight as rawVertWeight
from ...util.util import getRawNormal, computeStableVertices, stripCornerKey
from ...util.materials import TextureSave
from ...util.packet import boneIndex, finishPacket
from ...util.import_state import originalModel


def setModeSafe(obj, mode):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    with bpy.context.temp_override(active_object=obj, selected_objects=[obj], object=obj):
        bpy.ops.object.mode_set(mode=mode)


# fixes blender sometimes reporting stale/empty uv+attribute data right after loading a .blend
def refreshMeshState(obj):
    setModeSafe(obj, 'EDIT')
    setModeSafe(obj, 'OBJECT')


class MeshExportHelper:
    obj: bpy.types.Object
    mesh: bpy.types.Mesh
    
    bone_name: str
    
    def __init__(self, obj: bpy.types.Object, bone_name: str):
        self.obj = obj
        if obj.type != 'MESH':
            raise Exception(f"Invalid object {obj.name} with type {obj.type} passed to MeshExportHelper")
        self.mesh = obj.data
        self.bone_name = bone_name
    
    def getVertWeight(self, vert) -> int:
        return round(rawVertWeight(vert, self.obj, self.bone_name) * 4096)
    
    def kmsVertFromVert(self, vert) -> KMSVertex:
        return KMSVertex(round(vert.co.x), round(vert.co.y), round(vert.co.z), self.getVertWeight(vert))
    
    def kmsVertFromIndex(self, index) -> KMSVertex:
        return self.kmsVertFromVert(self.mesh.vertices[index])

def kmsNormFromLoop(mesh, loop, isFace: bool, stableVertices) -> KMSNormal:
    nrm = getRawNormal(mesh, loop.vertex_index, loop.normal, stableVertices)
    return KMSNormal(nrm.x * -4096, nrm.y * -4096, nrm.z * -4096, isFace)

def kmsUvFromLayerAndLoop(mesh, uvLayer: int, loopIndex: int) -> KMSUv:
    uv = mesh.uv_layers[uvLayer].uv[loopIndex].vector
    return KMSUv(uv.x * 4096, (1 - uv.y) * 4096)


def defaultPacketFlags(packet, parent_index, mesh_type):
    flag = 0
    if any(not 0 <= v.weight <= 4096 for v in packet.vertices):
        raise ValueError("KMS bone weights must be between 0 and 1")
    if any(v.weight != 4096 for v in packet.vertices):
        if parent_index < 0:
            raise ValueError("KMS parent weights require a parent bone")
        flag |= 0x1
    if mesh_type & 0x400:
        flag |= 0x4
    elif mesh_type & 0x200:
        flag |= 0x2
    for texture, uvs, bits in ((packet.colorMap, packet.uvs, 0x48), (packet.specularMap, packet.uvs2, 0x90)):
        if texture:
            if packet.vertices and (uvs is None or len(uvs) != len(packet.vertices)):
                raise ValueError("KMS texture is missing its UV layer")
            flag |= bits
    # the environment slot uses generated reflection coordinates, not a third UV layer.
    if packet.environmentMap:
        flag |= 0x220
    return flag


def main(kms_file: str, collection_name: str, ctxr_dir: str = None, ctxr_bak: str = 'never'):
    from ...util.export_mesh import prepared_meshes

    with prepared_meshes(bpy.data.collections[collection_name]):
        return _export(kms_file, collection_name, ctxr_dir, ctxr_bak)


def _export(kms_file, collection_name, ctxr_dir, ctxr_bak):
    kms = KMS()
    
    collection = bpy.data.collections[collection_name]
    original = originalModel(collection)
    
    amt = [x for x in collection.all_objects if x.type == "ARMATURE"][0]
    kms.header.kmsType = amt["kmsType"]
    kms.header.strcode = amt["strcode"]
    kms.header.pos = KMSVector3().set(amt.location)
    kms.header.minPos = KMSVector3().set(amt["bboxMin"])
    kms.header.maxPos = KMSVector3().set(amt["bboxMax"])
    
    worldBoundsMin = None
    worldBoundsMax = None

    setModeSafe(amt, 'EDIT')
    bones = amt.data.edit_bones
    orderedBones = sorted(bones, key=boneIndex)
    if [boneIndex(b) for b in orderedBones] != list(range(len(bones))):
        raise ValueError('KMS bone indices must be unique and contiguous')
    runtime = []
    for b in orderedBones:
        index = boneIndex(b)
        was_runtime = not original or index < original.header.numBones or index >= len(original.meshes)
        runtime.append(bool(b.get('sealouse_runtime_bone', was_runtime)))
    forceBoneCount = sum(runtime)
    if runtime != [True] * forceBoneCount + [False] * (len(bones) - forceBoneCount):
        raise ValueError('KMS extension bones must follow the runtime bones')
    
    texSave = TextureSave()
    
    for obj in collection.all_objects:
        if obj.type != "MESH":
            continue
        print("Exporting", obj.name)

        mesh = obj.data
        refreshMeshState(obj)
        if bpy.app.version < (4, 1):
            mesh.calc_normals_split()
        stableVertices = computeStableVertices(mesh)
        
        kmsMesh = KMSMesh()
        kmsMesh.flag = obj['flag'] if 'flag' in obj else 1
        
        meshIndex = int(obj['sealouse_mesh_index']) if 'sealouse_mesh_index' in obj else int(obj.name.split('Mesh')[1].split('.')[0])

        # return to armature edit mode after refreshing the mesh.
        setModeSafe(amt, 'EDIT')
        bones = amt.data.edit_bones
        bone = next((b for b in bones if boneIndex(b) == meshIndex), None)
        if bone is None:
            raise ValueError(f'{obj.name}: missing bone {meshIndex}')

        boneName = bone.name
        print(f"Making mesh for bone {bone.name}, pos {tuple(bone.head)}")
        kmsMesh.pos.set(bone.head)

        objLoc = bone.head + amt.location
        for v in mesh.vertices:
            wx, wy, wz = v.co.x + objLoc.x, v.co.y + objLoc.y, v.co.z + objLoc.z
            if worldBoundsMin is None:
                worldBoundsMin = KMSVector3(wx, wy, wz)
                worldBoundsMax = KMSVector3(wx, wy, wz)
            else:
                worldBoundsMin.x = min(worldBoundsMin.x, wx)
                worldBoundsMin.y = min(worldBoundsMin.y, wy)
                worldBoundsMin.z = min(worldBoundsMin.z, wz)
                worldBoundsMax.x = max(worldBoundsMax.x, wx)
                worldBoundsMax.y = max(worldBoundsMax.y, wy)
                worldBoundsMax.z = max(worldBoundsMax.z, wz)
        kmsMesh.minPos.set(obj.bound_box[0])
        kmsMesh.maxPos.set(obj.bound_box[6])

        kmsMesh.parentInd = -1
        if bone.parent:
            kmsMesh.parentInd = boneIndex(bone.parent)
            # parents can appear later in the file (cti_female).
            kmsMesh.pos.set(bone.head - bone.parent.head)
        if 'kms_attachment_parent' in obj:
            # these indices belong to the assembled model, not this parts library.
            kmsMesh.parentInd = obj['kms_attachment_parent']
            kmsMesh.parent = None
        
        # create a KMS packet for each material slot.
        for materialSlot in obj.material_slots:
            mat = materialSlot.material
            
            vertexGroup = KMSVertexGroup()
            if "flag" in mat:
                vertexGroup.flag = mat["flag"]
            
            vertexGroup.colorMap = texSave.get_map(mat, "colorMap")
            vertexGroup.specularMap = texSave.get_map(mat, "specularMap")
            vertexGroup.environmentMap = texSave.get_map(mat, "environmentMap")

            if len(mesh.uv_layers) > 0:
                vertexGroup.uvs = []
            if len(mesh.uv_layers) > 1:
                vertexGroup.uvs2 = []
            if len(mesh.uv_layers) > 2:
                vertexGroup.uvs3 = []
                
            kmsMesh.vertexGroups.append(vertexGroup)
        
        refreshMeshState(obj)

        allCornersWritten = [[] for _ in kmsMesh.vertexGroups]
        drawFlags = [[] for _ in kmsMesh.vertexGroups]
        flips = [False for _ in kmsMesh.vertexGroups]
        helper = MeshExportHelper(obj, boneName)
        flip = False
        for polygon in mesh.polygons:
            assert(polygon.loop_total == 3) # Not triangulated!
            polyMat = polygon.material_index
            vertexGroup = kmsMesh.vertexGroups[polyMat]
            cornersWritten = allCornersWritten[polyMat]
            flip = flips[polyMat]
            loopIndices = list(range(polygon.loop_start, polygon.loop_start + 3))
            loopIndices = [loopIndices[0], loopIndices[2], loopIndices[1]]
            vertexIndices = [mesh.loops[i].vertex_index for i in loopIndices]
            cornerKeys = [stripCornerKey(mesh, i, stableVertices) for i in loopIndices]
            if flip:
                other_check_index = 1
                compress_add_index = 2
            else:
                other_check_index = 2
                compress_add_index = 1
            if len(cornersWritten) >= 2 and cornerKeys[other_check_index] == cornersWritten[-1] and cornerKeys[0] == cornersWritten[-2]:
                drawFlags[polyMat].append(0 if flip else 0x20)
                # Optimize!
                cornersWritten.append(cornerKeys[compress_add_index])
                vertexGroup.vertices += [helper.kmsVertFromIndex(vertexIndices[compress_add_index])]
                vertexGroup.normals += [kmsNormFromLoop(mesh, mesh.loops[loopIndices[compress_add_index]], True, stableVertices)]
                if vertexGroup.uvs is not None:
                    vertexGroup.uvs += [kmsUvFromLayerAndLoop(mesh, 0, loopIndices[compress_add_index])]
                if vertexGroup.uvs2 is not None:
                    vertexGroup.uvs2 += [kmsUvFromLayerAndLoop(mesh, 1, loopIndices[compress_add_index])]
                if vertexGroup.uvs3 is not None:
                    vertexGroup.uvs3 += [kmsUvFromLayerAndLoop(mesh, 2, loopIndices[compress_add_index])]
                flip = not flip
            else:
                # add all three :(
                drawFlags[polyMat].extend((0x8000, 0x8000, 0x20))
                cornersWritten.extend(cornerKeys)
                vertexGroup.vertices += [helper.kmsVertFromIndex(vert) for vert in vertexIndices]
                vertexGroup.normals += [
                    kmsNormFromLoop(mesh, mesh.loops[loopIndices[0]], False, stableVertices),
                    kmsNormFromLoop(mesh, mesh.loops[loopIndices[1]], False, stableVertices),
                    kmsNormFromLoop(mesh, mesh.loops[loopIndices[2]], True, stableVertices),
                ]
                if vertexGroup.uvs is not None:
                    vertexGroup.uvs += [
                        kmsUvFromLayerAndLoop(mesh, 0, loopIndices[0]),
                        kmsUvFromLayerAndLoop(mesh, 0, loopIndices[1]),
                        kmsUvFromLayerAndLoop(mesh, 0, loopIndices[2])
                    ]
                if vertexGroup.uvs2 is not None:
                    vertexGroup.uvs2 += [
                        kmsUvFromLayerAndLoop(mesh, 1, loopIndices[0]),
                        kmsUvFromLayerAndLoop(mesh, 1, loopIndices[1]),
                        kmsUvFromLayerAndLoop(mesh, 1, loopIndices[2])
                    ]
                if vertexGroup.uvs3 is not None:
                    vertexGroup.uvs3 += [
                        kmsUvFromLayerAndLoop(mesh, 2, loopIndices[0]),
                        kmsUvFromLayerAndLoop(mesh, 2, loopIndices[1]),
                        kmsUvFromLayerAndLoop(mesh, 2, loopIndices[2])
                    ]
                flip = False
            flips[polyMat] = flip
        
        for materialSlot, vertexGroup, draws in zip(obj.material_slots, kmsMesh.vertexGroups, drawFlags):
            if "flag" not in materialSlot.material:
                vertexGroup.flag = defaultPacketFlags(vertexGroup, kmsMesh.parentInd, kmsMesh.flag)
            finishPacket(vertexGroup, materialSlot.material, draws, bool(kmsMesh.flag & 0x600))
        
        kms.meshes.append(kmsMesh)
    
    if worldBoundsMin is not None:
        # min/max against the original box - never shrink it, otherwise we'll break breakable objects (break break break)
        kms.header.minPos = KMSVector3(
            min(worldBoundsMin.x - kms.header.pos.x, kms.header.minPos.x),
            min(worldBoundsMin.y - kms.header.pos.y, kms.header.minPos.y),
            min(worldBoundsMin.z - kms.header.pos.z, kms.header.minPos.z),
        )
        kms.header.maxPos = KMSVector3(
            max(worldBoundsMax.x - kms.header.pos.x, kms.header.maxPos.x),
            max(worldBoundsMax.y - kms.header.pos.y, kms.header.maxPos.y),
            max(worldBoundsMax.z - kms.header.pos.z, kms.header.maxPos.z),
        )

    setModeSafe(amt, 'OBJECT')
    
    if ctxr_dir:
        print("Saving new CTXRs...")
        texSave.save_textures(ctxr_dir, ctxr_bak)
        print("CTXR COMPLETE :)")
    
    with atomic_output(kms_file) as f:
        from ...util.import_state import restore

        kms = restore(collection, kms)
        kms.writeToFile(f, forceBoneCount=forceBoneCount)
    return {'FINISHED'}
