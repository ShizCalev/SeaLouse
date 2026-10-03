import bpy
from ...util.atomic_output import atomic_output
from ..evm import *
from ...util.util import getRawNormal, computeStableVertices, stripCornerKey
from ...util.materials import TextureSave
from ...util.packet import boneIndex, textureFlags, finishPacket, evmWeights
from ...util.import_state import originalModel


def setModeSafe(obj, mode):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    with bpy.context.temp_override(active_object=obj, selected_objects=[obj], object=obj):
        bpy.ops.object.mode_set(mode=mode)


def refreshMeshState(obj):
    setModeSafe(obj, 'EDIT')
    setModeSafe(obj, 'OBJECT')

def evmVertFromVert(vert, isFace: bool, posScale: float) -> EVMVertex:
    # convert blender coordinates back to EVM position units.
    return EVMVertex(vert.co.x / posScale, vert.co.y / posScale, vert.co.z / posScale, isFace)

def evmNormFromLoop(mesh, loop, stableVertices) -> EVMNormal:
    nrm = getRawNormal(mesh, loop.vertex_index, loop.normal, stableVertices)
    return EVMNormal(nrm.x * -4096, nrm.y * -4096, nrm.z * -4096)

def evmUvFromLayerAndLoop(omesh, uvLayer: int, loopIndex: int) -> EVMUv:
    uv = omesh.uv_layers[uvLayer].uv[loopIndex].vector
    return EVMUv(uv.x * 4096, (1 - uv.y) * 4096)

def main(evm_file: str, collection_name: str, ctxr_dir: str = None):
    from ...util.export_mesh import prepared_meshes

    with prepared_meshes(bpy.data.collections[collection_name], evm=True):
        return _export(evm_file, collection_name, ctxr_dir)


def _export(evm_file, collection_name, ctxr_dir):
    evm = EVM()
    
    collection = bpy.data.collections[collection_name]
    original = originalModel(collection)
    
    amt = [x for x in collection.all_objects if x.type == "ARMATURE"][0]
    evm.header.strcode = amt["strcode"]
    evm.header.flag = amt["flag"]
    # DG_EVMTYPE_LARGE: use raw positions if set, else /16
    posScale = evmPosScale(evm.header.flag)
    evm.header.minPos = EVMVector3(amt["bboxMin"][0], amt["bboxMin"][1], amt["bboxMin"][2])
    evm.header.maxPos = EVMVector3(amt["bboxMax"][0], amt["bboxMax"][1], amt["bboxMax"][2])
    
    evm.bones = [EVMBone() for _ in range(len(amt.data.bones))]
    
    setModeSafe(amt, 'EDIT')
    
    fingerIndex = int(amt.get('fingerIndex', original.header.fingerIndex if original else len(evm.bones)))
    evm.header.fingerIndex = fingerIndex
    indices = {b.name: boneIndex(b, fingerIndex) for b in amt.data.edit_bones}
    if sorted(indices.values()) != list(range(len(evm.bones))):
        raise ValueError('EVM bone indices must be unique and contiguous')
    
    for bone in amt.data.edit_bones:
        print(bone.name)
        index = indices[bone.name]
        evmBone = evm.bones[index]
        savedFlag = original.bones[index].flag if original and index < len(original.bones) else 0
        evmBone.flag = int(bone.get('sealouse_bone_flag', savedFlag)) & 0xffffffff
        evmBone.worldPos.x = bone.head.x
        evmBone.worldPos.y = bone.head.y
        evmBone.worldPos.z = bone.head.z
        evmBone.relativePos.x = bone.head.x
        evmBone.relativePos.y = bone.head.y
        evmBone.relativePos.z = bone.head.z
        if bone.parent:
            evmBone.parentInd = indices[bone.parent.name]
            evmBone.relativePos.x -= bone.parent.head.x
            evmBone.relativePos.y -= bone.parent.head.y
            evmBone.relativePos.z -= bone.parent.head.z
    setModeSafe(amt, 'OBJECT')
    
    texSave = TextureSave()
    
    for i, obj in enumerate(collection.all_objects):
        if obj.type != "MESH":
            continue
        print("Exporting", obj.name)
        omesh = obj.data
        refreshMeshState(obj)
        if bpy.app.version < (4, 1):
            omesh.calc_normals_split()
        stableVertices = computeStableVertices(omesh)
        packets = []
        active_groups = {g.group for v in omesh.vertices for g in v.groups if g.weight > 0}
        group_indices = {}
        for i in active_groups:
            name = obj.vertex_groups[i].name
            if name not in indices:
                raise ValueError(f'{obj.name}: weight group {name} has no matching bone')
            group_indices[i] = indices[name]
        
        # create an EVM packet for each material slot.
        for materialSlot in obj.material_slots:
            mat = materialSlot.material
            mesh = EVMMesh()

            if "flag" in mat:
                mesh.flag = mat["flag"]
            
            mesh.colorMap = texSave.get_map(mat, "colorMap")
            mesh.specularMap = texSave.get_map(mat, "specularMap")
            mesh.environmentMap = texSave.get_map(mat, "environmentMap")
            if 'flag' not in mat:
                mesh.flag = textureFlags(mesh) | (4 if mat.use_backface_culling else 0)

            if len(omesh.uv_layers) > 0:
                mesh.uvs = []
            if len(omesh.uv_layers) > 1:
                mesh.uvs2 = []
            if len(omesh.uv_layers) > 2:
                mesh.uvs3 = []
            
            mesh.weights = []
                
            evm.meshes.append(mesh)
            packets.append(mesh)
        
        # Skinning tables - intermediate step for weights later
        for polygon in omesh.polygons:
            assert(polygon.loop_total == 3) # Not triangulated!
            polyMat = polygon.material_index
            mesh = packets[polyMat]
            
            loopIndices = list(range(polygon.loop_start, polygon.loop_start + 3))
            vertexIndices = [omesh.loops[j].vertex_index for j in loopIndices]
            for vertIndex in vertexIndices:
                vert = omesh.vertices[vertIndex]
                if len([g for g in vert.groups if g.weight > 0]) > 4:
                    raise ValueError('EVM vertices support at most four nonzero bone weights')
                for bone in vert.groups:
                    if bone.weight <= 0:
                        continue
                    index = group_indices[bone.group]
                    if index not in mesh.skinningTable:
                        if mesh.numSkin == 8:
                            raise Exception("Material %d (%s) has too many weights" % (polyMat, obj.material_slots[polyMat].name))
                        mesh.skinningTable[mesh.numSkin] = index
                        mesh.numSkin += 1
        
        # Join skinning tables where possible
        startJ = 0
        superSkinningTable = set()
        for j, mesh in enumerate(packets):
            
            if (len(superSkinningTable.union([x for x in mesh.skinningTable if x != 0xFF])) <= 8):
                superSkinningTable = superSkinningTable.union([x for x in mesh.skinningTable if x != 0xff])
            else: # table full
                processedSkinningTable = sorted(list(superSkinningTable))
                while len(processedSkinningTable) < 8:
                    processedSkinningTable.append(255)
                #print("Using processedSkinningTable:", processedSkinningTable, "failed to join with", mesh.skinningTable)
                for k in range(startJ, j):
                    packets[k].skinningTable = processedSkinningTable
                startJ = j
                superSkinningTable = set([x for x in mesh.skinningTable if x != 0xff])
            mesh.numSkin = 0
        # sort the remaining packet palettes
        for j in range(startJ, len(packets)):
            packets[j].skinningTable.sort()
                
        allCornersWritten = [[] for _ in packets]
        drawFlags = [[] for _ in packets]
        flips = [False for _ in packets]
        
        flip = False
        for polygon in omesh.polygons:
            polyMat = polygon.material_index
            vertexGroup = packets[polyMat]
            
            
            cornersWritten = allCornersWritten[polyMat]
            flip = flips[polyMat]
            loopIndices = list(range(polygon.loop_start, polygon.loop_start + 3))
            loopIndices = [loopIndices[0], loopIndices[2], loopIndices[1]]
            vertexIndices = [omesh.loops[j].vertex_index for j in loopIndices]
            cornerKeys = [stripCornerKey(omesh, j, stableVertices) for j in loopIndices]
            if flip:
                other_check_index = 1
                compress_add_index = 2
            else:
                other_check_index = 2
                compress_add_index = 1
            if len(cornersWritten) >= 2 and cornerKeys[other_check_index] == cornersWritten[-1] and cornerKeys[0] == cornersWritten[-2]:
                drawFlags[polyMat].append(0 if flip else 0x20)
                # Optimize, baby!
                cornersWritten.append(cornerKeys[compress_add_index])
                vert3 = omesh.vertices[vertexIndices[compress_add_index]]
                vertexGroup.vertices += [evmVertFromVert(vert3, True, posScale)]
                vertexGroup.normals += [evmNormFromLoop(omesh, omesh.loops[loopIndices[compress_add_index]], stableVertices)]
                if len(omesh.uv_layers) > 0:
                    vertexGroup.uvs += [evmUvFromLayerAndLoop(omesh, 0, loopIndices[compress_add_index])]
                if len(omesh.uv_layers) > 1:
                    vertexGroup.uvs2 += [evmUvFromLayerAndLoop(omesh, 1, loopIndices[compress_add_index])]
                if len(omesh.uv_layers) > 2:
                    vertexGroup.uvs3 += [evmUvFromLayerAndLoop(omesh, 2, loopIndices[compress_add_index])]
                vertexGroup.weights.append(evmWeights(vert3, vertexGroup, group_indices))
                flip = not flip
            else:
                # add all three :(
                drawFlags[polyMat].extend((0x8000, 0x8000, 0x20))
                
                flip = False
                cornersWritten.extend(cornerKeys)
                vert1 = omesh.vertices[vertexIndices[0]]
                vert2 = omesh.vertices[vertexIndices[1]]
                vert3 = omesh.vertices[vertexIndices[2]]
                vertexGroup.vertices += [
                    evmVertFromVert(vert1, False, posScale),
                    evmVertFromVert(vert2, False, posScale),
                    evmVertFromVert(vert3, True, posScale),
                ]
                vertexGroup.normals += [evmNormFromLoop(omesh, omesh.loops[loop], stableVertices) for loop in loopIndices]
                if len(obj.data.uv_layers) > 0:
                    vertexGroup.uvs += [
                        evmUvFromLayerAndLoop(omesh, 0, loopIndices[0]),
                        evmUvFromLayerAndLoop(omesh, 0, loopIndices[1]),
                        evmUvFromLayerAndLoop(omesh, 0, loopIndices[2])
                    ]
                if len(obj.data.uv_layers) > 1:
                    vertexGroup.uvs2 += [
                        evmUvFromLayerAndLoop(omesh, 1, loopIndices[0]),
                        evmUvFromLayerAndLoop(omesh, 1, loopIndices[1]),
                        evmUvFromLayerAndLoop(omesh, 1, loopIndices[2])
                    ]
                if len(obj.data.uv_layers) > 2:
                    vertexGroup.uvs3 += [
                        evmUvFromLayerAndLoop(omesh, 2, loopIndices[0]),
                        evmUvFromLayerAndLoop(omesh, 2, loopIndices[1]),
                        evmUvFromLayerAndLoop(omesh, 2, loopIndices[2])
                    ]
                vertexGroup.weights.extend(evmWeights(v, vertexGroup, group_indices) for v in (vert1, vert2, vert3))
            flips[polyMat] = flip
        
        for slot, packet, draws in zip(obj.material_slots, packets, drawFlags):
            finishPacket(packet, slot.material, draws, bool(packet.flag & 4), evm=True)
        
    if ctxr_dir:
        print("Saving new CTXRs...")
        texSave.save_textures(ctxr_dir)
        print("CTXR COMPLETE :)")
    
    with atomic_output(evm_file) as f:
        update_bounds(evm)
        from ...util.import_state import restore

        evm = restore(collection, evm)
        evm.writeToFile(f)
    return {'FINISHED'}


def update_bounds(evm):
    # each bone's bounding box includes all vertices with a nonzero weight on it.
    points = [[] for bone in evm.bones]
    scale = evmPosScale(evm.header.flag)
    for packet in evm.meshes:
        for v, w in zip(packet.vertices, packet.weights):
            p = (v.x * scale, v.y * scale, v.z * scale)
            for axis, value in zip(('x', 'y', 'z'), p):
                setattr(evm.header.minPos, axis, min(getattr(evm.header.minPos, axis), value))
                setattr(evm.header.maxPos, axis, max(getattr(evm.header.maxPos, axis), value))
            for weight, index in zip(w.weights, w.indices):
                if weight:
                    bone = packet.skinningTable[index // 4]
                    if bone != 255:
                        points[bone].append(p)
    for bone, positions in zip(evm.bones, points):
        positions = positions or [bone.worldPos.xyz()]
        bone.minPos = EVMVector4(*(min(p[k] for p in positions) for k in range(3)), 0)
        bone.maxPos = EVMVector4(*(max(p[k] for p in positions) for k in range(3)), 0)
