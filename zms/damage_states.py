from contextlib import contextmanager
import bpy
from mathutils import Matrix


# cbx visibility masks
STATES = (
    ('Intact', (0, 1, 13, 14, 15, 16)),
    ('Damage 1', (0, 2, 14, 15, 16, 17, 18)),
    ('Damage 2', (0, 3, 15, 16, 17, 18)),
    ('Damage 3', (0, 4, 6, 8, 10, 12, 16, 17, 18)),
    ('Damage 4', (0, 5, 6, 7, 8, 9, 10, 11, 12, 17)),
    ('Broken debris', (5, 6, 7, 8, 9, 10, 11, 12, 17)),
)
BODY_IDS = {0x253b8a, 0x25bb8a, 0x263b8a, 0x26bb8a, 0x273b8a}


def add_previews(archive, collection, ident, model):
    if ident not in BODY_IDS or len(model.meshes) != 19:
        return
    bpy.context.view_layer.update()
    sources = [o for o in collection.objects if o.type == 'MESH']
    arm = next(o for o in collection.objects if o.type == 'ARMATURE')
    column = arm.parent.matrix_world.translation.x
    spacing = max(1.0, (model.header.maxPos.z - model.header.minPos.z) * 0.001 * 1.25)
    for state, (label, parts) in enumerate(STATES):
        root = bpy.data.objects.new(f'{ident:08x} - {label}', None)
        archive.objects.link(root)
        root.location = (column, state * spacing, 0)
        root.empty_display_size = 0.15
        root.show_name = True
        for part in parts:
            source = sources[part]
            obj = source.copy()
            obj.name = f'{ident:08x} - {label} - part {part:02}'
            archive.objects.link(obj)
            obj.modifiers.clear()
            obj.parent = root
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = Matrix.Translation((-column, 0, 0)) @ source.matrix_world
            obj.lock_location = obj.lock_rotation = obj.lock_scale = (True,) * 3
            obj['zms_state_source'] = source
            obj['zms_state_root'] = root
            obj['zms_state_matrix'] = [v for row in obj.matrix_basis for v in row]
            obj['zms_state_id'] = state * 19 + part
    collection['zms_state_parts'] = [state * 19 + part for state, (_, parts) in enumerate(STATES) for part in parts]
    for obj in collection.objects:
        obj.hide_set(True)
        obj.hide_render = True


def validate_previews(archive):
    if any(o.type == 'MESH' and 'zms_state_source' not in o for o in archive.objects):
        raise ValueError('Join added geometry to a damage-state part before exporting')
    for collection in archive.children:
        expected = collection.get('zms_state_parts')
        if expected is None:
            continue
        sources = set(collection.objects)
        previews = [o for o in archive.objects if o.get('zms_state_source') in sources]
        if sorted(o.get('zms_state_id', -1) for o in previews) != sorted(expected):
            raise ValueError('ZMS state preview objects were added or deleted; edit their geometry in Edit Mode')
        for obj in previews:
            source = obj['zms_state_source']
            if obj.data != source.data:
                raise ValueError(f'{obj.name}: keep damage-state geometry linked to its source part')
            if obj.parent is None or obj.parent != obj.get('zms_state_root') or obj.matrix_parent_inverse != Matrix.Identity(4):
                raise ValueError(f'{obj.name}: damage-state parenting changed')
            if [v for row in obj.matrix_basis for v in row] != list(obj['zms_state_matrix']):
                raise ValueError(f'{obj.name}: move vertices in Edit Mode, not the preview object')
            if obj.modifiers or obj.constraints or [g.name for g in obj.vertex_groups] != [g.name for g in source.vertex_groups]:
                raise ValueError(f'{obj.name}: edit modifiers and bone groups on the source member')
            if obj.get('flag') != source.get('flag') or any(slot.link != 'DATA' for slot in obj.material_slots):
                raise ValueError(f'{obj.name}: edit flags and material slots on the source member')


@contextmanager
def export_sources(archive):
    active = bpy.context.view_layer.objects.active
    selected = list(bpy.context.selected_objects)
    mode = active.mode if active else 'OBJECT'
    hidden = [(o, o.hide_get()) for c in archive.children for o in c.all_objects]
    try:
        if active and mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        validate_previews(archive)
        for obj, _ in hidden:
            obj.hide_set(False)
        yield
    finally:
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj, value in hidden:
            obj.hide_set(value)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active
        if active and mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=mode)
