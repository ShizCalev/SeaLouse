import io
import os
import tempfile
from pathlib import Path
import bpy
from bpy_extras.io_utils import ImportHelper, ExportHelper
from .zms import ZMS
from ..tri.texture_sources import TextureChoice, draw_choice, run_import
from ..kms.kms import KMS
from ..util.atomic_output import atomic_output
from ..util.util import texture_modes, defaultTexturePaths, changeTextureMode, gv_strcode, tri_lookup_path


def import_archive(path, ctxr_path=None, overwrite=False, tri_dir=None, side_by_side=True, damage_states=True):
    from ..kms.importer import kms_importer

    with open(path, 'rb') as stream:
        archive = ZMS().fromFile(stream)
    if ctxr_path:
        (Path(path).parent / 'sealouse_extract').mkdir(exist_ok=True)
    if tri_dir:
        from ..tri.tri import TRI

        codes = {model.header.strcode for ident, model in archive.members}
        candidates = {}
        for directory in (Path(path).parent, Path(tri_dir)):
            for candidate in directory.glob('*.tri'):
                stem = candidate.stem
                code = int(stem, 16) if len(stem) == 6 and all(c in '0123456789abcdefABCDEF' for c in stem) else gv_strcode(stem)
                candidates.setdefault(code, candidate)
        with open(tri_lookup_path) as mapping:
            for line in mapping:
                fields = line.split()
                if len(fields) >= 3 and int(fields[1]) in codes:
                    candidate = Path(tri_dir) / fields[2]
                    if candidate.is_file():
                        candidates.setdefault(int(fields[1]), candidate)
        extract = Path(path).parent / 'sealouse_extract'
        for code in codes:
            if code in candidates:
                extract.mkdir(exist_ok=True)
                with candidates[code].open('rb') as stream:
                    tri = TRI()
                    tri.fromFile(stream)
                tri.dumpTextures(str(extract))
    root = bpy.data.collections.new(Path(path).stem + '.zms')
    bpy.context.scene.collection.children.link(root)
    root['zms_ids'] = [ident for ident, model in archive.members]
    x = 0.0
    for ident, model in archive.members:
        before = set(bpy.data.collections)
        name = str(Path(path).with_name(f'{ident:08x}.kms'))
        kms_importer.import_model(model, name, ctxr_path, overwrite, tri_dir=tri_dir)
        col = next(c for c in bpy.data.collections if c not in before and c.name != 'KMS')
        root.children.link(col)
        bpy.data.collections['KMS'].children.unlink(col)
        col['zms_id'] = ident
        arm = next(o for o in col.objects if o.type == 'ARMATURE')
        wrapper = arm.parent
        if side_by_side:
            wrapper.location.x = x
            x += max(1.0, (model.header.maxPos.x - model.header.minPos.x) * 0.001 * 1.25)
        # The lineup offset is display-only; member positions stay in their own model space.
        wrapper.lock_location = (True, True, True)
        wrapper.lock_rotation = (True, True, True)
        wrapper.lock_scale = (True, True, True)
        if damage_states:
            from .damage_states import add_previews

            add_previews(root, col, ident, model)
    previews = [o for o in root.objects if 'zms_state_source' in o]
    if previews:
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        first = next((o for o in previews if len(o.data.polygons)), previews[0])
        first.select_set(True)
        bpy.context.view_layer.objects.active = first
    return root


def export_archive(path, root, make_cmdl=True):
    from .damage_states import export_sources

    with export_sources(root):
        return _export_archive(path, root, make_cmdl)


def _export_archive(path, root, make_cmdl=True):
    from ..kms.exporter import kms_exporter
    from ..cmdl.exporter.cmdl_cooker import cook

    ids = list(root.get('zms_ids', []))
    children = list(root.children)
    if not ids or len(children) != len(ids) or sorted(c.get('zms_id', -1) for c in children) != sorted(ids):
        raise ValueError("ZMS members were removed or their IDs changed")
    archive = ZMS()
    supplements = []
    with tempfile.TemporaryDirectory(prefix='sealouse-zms-') as directory:
        for ident in ids:
            col = next(c for c in children if c['zms_id'] == ident)
            if len([o for o in col.all_objects if o.type == 'ARMATURE']) != 1:
                raise ValueError(f'{col.name}: keep the member armature intact')
            member_path = Path(directory) / f'{ident:08x}.kms'
            kms_exporter.main(str(member_path), col.name)
            with member_path.open('rb') as stream:
                model = KMS().fromFile(stream)
            archive.members.append((ident, model))
            if make_cmdl:
                stream = io.BytesIO()
                cook(model, False).writeToFile(stream)
                supplements.append((ident, stream.getvalue()))
        stream = io.BytesIO()
        archive.writeToFile(stream)
        output = stream.getvalue()
    with atomic_output(path) as stream:
        stream.write(output)
    cmdl_dir = Path(path).with_suffix('') / '_win'
    if supplements:
        cmdl_dir.mkdir(parents=True, exist_ok=True)
    for ident, data in supplements:
        with atomic_output(cmdl_dir / f'{ident:08x}.cmdl') as stream:
            stream.write(data)


class ImportMgsZms(bpy.types.Operator, ImportHelper):
    bl_idname = 'import_scene.zms_data'
    bl_label = 'Import ZMS Data'
    bl_options = {'PRESET'}
    filename_ext = '.zms'
    filter_glob: bpy.props.StringProperty(default='*.zms', options={'HIDDEN'})
    reset_blend: bpy.props.BoolProperty(name='Reset Blender Scene on Import', default=True)
    side_by_side: bpy.props.BoolProperty(name='Display members side by side', default=True)
    damage_states: bpy.props.BoolProperty(name='Display damage states', default=True, description='Show cbx body damage states in rows; edit shared parts in Edit Mode')
    texture_mode: bpy.props.EnumProperty(name='Textures', items=texture_modes, default='ctxr', update=changeTextureMode)
    texture_path: bpy.props.StringProperty(name='Load Path:', default=defaultTexturePaths[2])
    texture_overwrite: bpy.props.BoolProperty(name='Re-extract existing', default=False)
    texture_selections: bpy.props.StringProperty(default='{}', options={'HIDDEN', 'SKIP_SAVE'})
    texture_options: bpy.props.CollectionProperty(type=TextureChoice, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        return run_import(self, context)

    def import_models(self, context):
        from ..kms.importer import kms_importer

        try:
            # Check the archive before clearing the current scene.
            with open(self.filepath, 'rb') as stream:
                ZMS().fromFile(stream)
            if self.reset_blend:
                kms_importer.reset_blend()
            texture_path = os.path.join(os.path.dirname(self.filepath), self.texture_path)
            import_archive(self.filepath, texture_path if self.texture_mode == 'ctxr' else None, self.texture_overwrite,
                           texture_path if self.texture_mode == 'tri' else None, self.side_by_side, self.damage_states)
        except (ValueError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}

    def draw(self, context):
        if draw_choice(self, self.layout):
            return
        self.layout.prop(self, 'reset_blend')
        self.layout.prop(self, 'side_by_side')
        self.layout.prop(self, 'damage_states')
        self.layout.prop(self, 'texture_mode')
        if self.texture_mode != 'none':
            self.layout.prop(self, 'texture_path')
            self.layout.prop(self, 'texture_overwrite')


class ExportMgsZms(bpy.types.Operator, ExportHelper):
    bl_idname = 'export_scene.zms_data'
    bl_label = 'Export ZMS Data'
    bl_options = {'PRESET'}
    filename_ext = '.zms'
    filter_glob: bpy.props.StringProperty(default='*.zms', options={'HIDDEN'})
    collection_name: bpy.props.StringProperty(name='Source archive')
    make_cmdl: bpy.props.BoolProperty(name='Generate CMDL supplements', default=True)

    def invoke(self, context, event):
        archives = [c for c in bpy.data.collections if 'zms_ids' in c]
        selected = [c for c in archives if context.object and context.object.name in c.all_objects]
        if len(selected) == 1 or len(archives) == 1:
            root = (selected or archives)[0]
            self.collection_name = root.name
            self.filepath = root.name if root.name.endswith('.zms') else root.name + '.zms'
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        root = bpy.data.collections.get(self.collection_name)
        if root is None or 'zms_ids' not in root:
            self.report({'ERROR'}, 'Choose an imported ZMS archive collection')
            return {'CANCELLED'}
        try:
            export_archive(self.filepath, root, self.make_cmdl)
        except (ValueError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}

    def draw(self, context):
        self.layout.prop_search(self, 'collection_name', bpy.data, 'collections')
        self.layout.prop(self, 'make_cmdl')
