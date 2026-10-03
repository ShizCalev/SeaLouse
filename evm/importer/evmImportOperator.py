import bpy
from bpy_extras.io_utils import ImportHelper
import os
from ...config import evmConfig
from ...tri.texture_sources import TextureChoice, draw_choice, run_import
from ...util.util import replaceExt, texture_modes, changeTextureMode, defaultTexturePaths, triNameFromModel, triPathFromHashFallback

class ImportMgsEvm(bpy.types.Operator, ImportHelper):
    '''Load an MGS2 EVM File.'''
    bl_idname = "import_scene.evm_data"
    bl_label = "Import EVM Data"
    bl_options = {'PRESET'}
    filename_ext = ".evm"
    filter_glob: bpy.props.StringProperty(default="*.evm", options={'HIDDEN'})

    reset_blend: bpy.props.BoolProperty(name="Reset Blender Scene on Import", default=evmConfig['import.reset'])
    texture_mode: bpy.props.EnumProperty(name="Textures", items=texture_modes, default=evmConfig['import.texmode'], update=changeTextureMode)
    texture_path: bpy.props.StringProperty(name="Load Path:", default=defaultTexturePaths[evmConfig['import.texmode']])
    texture_overwrite: bpy.props.BoolProperty(name="Re-extract existing", default=evmConfig['import.ctxr_replace'])
    texture_selections: bpy.props.StringProperty(default='{}', options={'HIDDEN', 'SKIP_SAVE'})
    texture_options: bpy.props.CollectionProperty(type=TextureChoice, options={'HIDDEN', 'SKIP_SAVE'})

    files: bpy.props.CollectionProperty(
        name="EVM files",
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN","SKIP_SAVE"},
    )

    directory: bpy.props.StringProperty(
        subtype='DIR_PATH',
    )


    def execute(self, context):
        return run_import(self, context)

    def import_models(self, context):
        from . import evm_importer
        from ...tri.tri import TRI
        if self.reset_blend:
            evm_importer.reset_blend()

        for file in self.files:
            evm_path = os.path.join(self.directory, file.name)
    
            print("Loading", evm_path, "with textures", self.texture_mode)
            dirname, evm_name = os.path.split(evm_path)
            if self.texture_mode != 'none':
                extract_path = os.path.join(dirname, "sealouse_extract")
                os.makedirs(extract_path, exist_ok=True)
            if self.texture_mode == 'tri':
                if os.path.isabs(self.texture_path):
                    tri_dir = self.texture_path
                else:
                    tri_dir = os.path.join(dirname, self.texture_path)
                tri_name = triNameFromModel(evm_path, "evm")

                if tri_name is None or not os.path.exists(os.path.join(tri_dir, tri_name)):
                    tri_path = os.path.join(tri_dir, replaceExt(evm_name, "tri"))
                else:
                    tri_path = os.path.join(tri_dir, tri_name)

                if not os.path.exists(tri_path):
                    hashed_path = triPathFromHashFallback(evm_path, "evm")
                    if hashed_path is not None:
                        tri_path = hashed_path

                print("Attempting to load TRI:", tri_path)
                if os.path.exists(tri_path):
                    tri = TRI()
                    with open(tri_path, "rb") as f:
                        tri.fromFile(f)
                    tri.dumpTextures(extract_path)
    
            if self.texture_mode == 'ctxr':
                # let the evm loader extract only the textures this model uses.
                if os.path.isabs(self.texture_path):
                    evm_importer.main(evm_path, self.texture_path, self.texture_overwrite)
                else:
                    evm_importer.main(evm_path, os.path.join(dirname, self.texture_path), self.texture_overwrite)
            else:
                evm_importer.main(evm_path, tri_dir = tri_dir if self.texture_mode == 'tri' else None)
            
        return {'FINISHED'}
        
    def draw(self, context):
        if draw_choice(self, self.layout):
            return
        layout = self.layout
        col = layout.column()
        col.prop(self, "reset_blend")
        col.prop(self, "texture_mode")
        if self.texture_mode != 'none':
            col.prop(self, "texture_path")
        if self.texture_mode == 'ctxr':
            col.prop(self, "texture_overwrite")

