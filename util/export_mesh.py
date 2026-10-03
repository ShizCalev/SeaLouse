from contextlib import contextmanager
import bpy
import bmesh
from mathutils import Matrix
from .util import NORMAL_STATE


@contextmanager
def prepared_meshes(collection, evm=False):
    # Triangulate and bake object edits on temporary meshes
    saved = []
    arm = next(o for o in collection.all_objects if o.type == 'ARMATURE')
    try:
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in collection.all_objects:
            if obj.type != 'MESH':
                continue
            original = obj.data
            unweighted = [v.index for v in original.vertices if not any(g.weight > 0 for g in v.groups)]
            if unweighted and len(obj.vertex_groups) != 1:
                raise ValueError(f'{obj.name}: {len(unweighted)} vertices have no bone weights; assign them before exporting')
            reference = obj.get('sealouse_import_local_matrix')
            if reference is None:
                bind = Matrix.Identity(4)
                if not evm:
                    try:
                        index = int(obj['sealouse_mesh_index']) if 'sealouse_mesh_index' in obj else int(obj.name.split('Mesh')[1].split('.')[0])
                    except (ValueError, IndexError):
                        raise ValueError(f'{obj.name}: join new KMS geometry to its bone mesh before exporting')
                    from .packet import boneIndex
                    bone = next(b for b in arm.data.bones if boneIndex(b) == index)
                    bind = Matrix.Translation(bone.head_local)
                reference = [c for row in bind for c in row]
            local = obj.matrix_parent_inverse @ obj.matrix_basis
            transformed = reference is not None and list(reference) != [c for row in local for c in row]
            modifiers = [m for m in obj.modifiers if m.type != 'ARMATURE' and m.show_viewport]
            if not transformed and not modifiers and not unweighted and all(len(p.vertices) == 3 for p in original.polygons):
                continue
            if modifiers:
                armatures = [m for m in obj.modifiers if m.type == 'ARMATURE' and m.show_viewport]
                try:
                    for m in armatures:
                        m.show_viewport = False
                    deps = bpy.context.evaluated_depsgraph_get()
                    deps.update()
                    copy = bpy.data.meshes.new_from_object(obj.evaluated_get(deps), preserve_all_data_layers=True, depsgraph=deps)
                finally:
                    for m in armatures:
                        m.show_viewport = True
            else:
                copy = original.copy()
            saved.append((obj, original, copy))
            obj.data = copy
            if unweighted:
                obj.vertex_groups[0].add([v.index for v in copy.vertices if not any(g.weight > 0 for g in v.groups)], 1.0, 'REPLACE')
            if transformed:
                bind = Matrix([reference[i : i + 4] for i in range(0, 16, 4)])
                transform = bind.inverted() @ local
                normals = [(transform.to_3x3().inverted().transposed() @ l.normal).normalized() for l in copy.loops]
                copy.transform(transform)
                if copy.has_custom_normals:
                    copy.normals_split_custom_set(normals)
                if NORMAL_STATE in copy:
                    del copy[NORMAL_STATE]
            if any(len(p.vertices) != 3 for p in copy.polygons):
                bm = bmesh.new()
                try:
                    bm.from_mesh(copy)
                    bmesh.ops.triangulate(bm, faces=list(bm.faces))
                    bm.to_mesh(copy)
                finally:
                    bm.free()
            copy.update()
        yield
    finally:
        for obj, original, copy in reversed(saved):
            obj.data = original
            bpy.data.meshes.remove(copy)
