import gzip
import json
import hashlib
import struct
import tempfile
from functools import lru_cache
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
import bpy

_choices = ContextVar('texture_name_choices', default=None)
_warnings = ContextVar('texture_source_warnings', default=None)


@lru_cache(maxsize=1)
def source_mapping():
    with gzip.open(Path(__file__).with_name('texture_sources.json.gz'), 'rt', encoding='utf-8') as stream:
        return json.load(stream)


@lru_cache(maxsize=1)
def texture_hashes():
    with Path(__file__).with_name('texture_hashes.json').open(encoding='utf-8') as stream:
        return json.load(stream)


def texture_hash(image):
    image = image.convert('RGBA')
    header = struct.pack('<BBBHHBHHHHBB', 0, 0, 2, 0, 0, 0, 0, 0, *image.size, 32, 32)
    # flatlist names use the dumped tga's md5
    return hashlib.md5(header + image.tobytes('raw', 'BGRA')).hexdigest()


def pair_key(tri_code, texture_id):
    return f'{tri_code:06x}/{texture_id:06x}'


@lru_cache(maxsize=None)
def versions(tri_code, texture_id):
    names = {}
    for row in source_mapping().get(str(texture_id), []):
        if int(row[5], 16) == tri_code:
            names.setdefault(row[0], set()).add(row[1])
    return {name: sorted(stages) for name, stages in sorted(names.items())}


def texture_name(tri_code, texture_id):
    names = versions(tri_code, texture_id)
    if not names:
        return None
    key = pair_key(tri_code, texture_id)
    selected = (_choices.get() or {}).get(key)
    if selected == 'SKIP' or (selected and selected.startswith('TRI:')):
        return None
    if selected in names:
        return selected
    if len(names) == 1:
        return next(iter(names))
    raise ValueError(f'Choose a texture name for {key}: ' + ', '.join(names))


@contextmanager
def import_choices(choices):
    warnings = set()
    token = _choices.set(choices)
    warning_token = _warnings.set(warnings)
    try:
        yield warnings
    finally:
        _choices.reset(token)
        _warnings.reset(warning_token)


def warn_ambiguity(texture_id):
    warnings = _warnings.get()
    if warnings is not None:
        warnings.add(f'{texture_id:06x}')


def requested_sources(operator):
    from ..kms.kms import KMS
    from ..evm.evm import EVM
    from ..zms.zms import ZMS

    if operator.filename_ext == '.zms':
        paths = [Path(operator.filepath)]
    else:
        paths = [Path(operator.directory) / file.name for file in operator.files]
    pairs = {}
    for path in dict.fromkeys(paths):
        with path.open('rb') as stream:
            if path.suffix.lower() == '.zms':
                models = [model for ident, model in ZMS().fromFile(stream).members]
            else:
                models = [(EVM() if path.suffix.lower() == '.evm' else KMS()).fromFile(stream)]
        for model in models:
            packets = model.meshes if isinstance(model, EVM) else [packet for mesh in model.meshes for packet in mesh.vertexGroups]
            for packet in packets:
                for texture_id in (packet.colorMap, packet.specularMap, packet.environmentMap):
                    if texture_id:
                        pairs.setdefault((model.header.strcode, texture_id), set()).add(path.parent.resolve())
    return pairs


def requested_pairs(operator):
    return sorted(requested_sources(operator))


def texture_override(tri_code, texture_id):
    selected = (_choices.get() or {}).get(pair_key(tri_code, texture_id), '')
    return selected if selected == 'SKIP' or selected.startswith('TRI:') else None


def dump_texture(path, texture_id, folder):
    from .tri import TRI

    with Path(path).open('rb') as stream:
        archive = TRI()
        archive.fromFile(stream)
    return archive.dumpById(str(folder), texture_id)


@lru_cache(maxsize=1)
def legacy_names():
    from ..ctxr.ctxr import ctxr_lookup_path

    with open(ctxr_lookup_path) as stream:
        return {int(Path(parts[1]).stem): Path(parts[2]).stem for parts in (line.split() for line in stream) if len(parts) >= 3}


def import_options(operator, tri_code, texture_id, directories, cache, folder):
    from PIL import Image
    from ..util.util import gv_strcode

    names = versions(tri_code, texture_id)
    options = dict(names)
    for name in names or [None]:
        custom = set()
        for directory in sorted(directories):
            filename = name or legacy_names().get(texture_id, f'{texture_id:06x}')
            ctxr = directory / operator.texture_path / (filename + '.ctxr')
            dds = directory / 'sealouse_extract' / (filename + '.dds')
            if ctxr.is_file() or (dds.is_file() and not operator.texture_overwrite):
                continue
            with import_choices({pair_key(tri_code, texture_id): name} if name else {}):
                paths = set(candidates(texture_id, directory, tri_code=tri_code))
            for path in directory.glob('*.tri'):
                stem = path.stem
                code = int(stem, 16) if len(stem) == 6 and all(c in '0123456789abcdefABCDEF' for c in stem) else gv_strcode(stem)
                if code == tri_code:
                    paths.add(path)
            found = {}
            for path in sorted(paths):
                key = (path, texture_id)
                if key not in cache:
                    try:
                        dumped = dump_texture(path, texture_id, folder)
                        cache[key] = None
                        if dumped:
                            with Image.open(dumped) as image:
                                cache[key] = texture_hash(image)
                    except (OSError, ValueError, TypeError, IndexError, struct.error):
                        cache[key] = None
                if cache[key] is not None:
                    found[path] = cache[key]
            expected = texture_hashes().get(name)
            verified = expected in found.values() if expected else len(names) == 1 and len(set(found.values())) == 1
            if not verified:
                custom.update(found)
        for path in sorted(custom):
            options['TRI:' + str(path)] = [str(path)]
    if any(key.startswith('TRI:') for key in options):
        options['SKIP'] = []
    return options


@lru_cache(maxsize=None)
def choice_items(names):
    names = json.loads(names) if names else {}
    items = [('CHOOSE', 'Choose a texture...', '')]
    for name, stages in names.items():
        if name.startswith('TRI:'):
            items.append((name, 'Use local/custom TRI: ' + Path(name[4:]).name, name[4:]))
        elif name == 'SKIP':
            items.append((name, 'Leave texture unloaded', 'Do not load a texture for this ID'))
        else:
            items.append((name, name, 'Stages: ' + ', '.join(stages)))
    return items


def texture_choices(self, context):
    return choice_items(self.names)


class TextureChoice(bpy.types.PropertyGroup):
    pair: bpy.props.StringProperty()
    names: bpy.props.StringProperty(default='{}')
    texture_variant: bpy.props.EnumProperty(name='Texture name', items=texture_choices)


def draw_choice(operator, layout):
    if not operator.texture_options:
        return False
    for choice in operator.texture_options:
        box = layout.box()
        box.label(text='TRI / texture ID: ' + choice.pair)
        box.prop(choice, 'texture_variant')
        stages = json.loads(choice.names).get(choice.texture_variant, [])
        if choice.texture_variant.startswith('TRI:'):
            box.label(text='File: ' + choice.texture_variant[4:])
            box.label(text='Use these pixels without checking the stock texture hash.')
        elif stages:
            box.label(text='Stages: ' + ', '.join(stages))
    return True


def run_import(operator, context):
    import bpy

    try:
        choices = json.loads(operator.texture_selections)
        for choice in operator.texture_options:
            if choice.texture_variant not in json.loads(choice.names):
                operator.report({'ERROR'}, 'Select a texture source before importing')
                return {'CANCELLED'}
            choices[choice.pair] = choice.texture_variant
        operator.texture_selections = json.dumps(choices)
        operator.texture_options.clear()
        if operator.texture_mode == 'ctxr':
            cache = {}
            with tempfile.TemporaryDirectory(prefix='sealouse-tri-choice-') as folder:
                for (tri_code, texture_id), directories in sorted(requested_sources(operator).items()):
                    key = pair_key(tri_code, texture_id)
                    selected = choices.get(key, '')
                    if selected == 'SKIP':
                        continue
                    if selected.startswith('TRI:'):
                        if dump_texture(selected[4:], texture_id, folder) is None:
                            raise ValueError(f'{selected[4:]}: texture {texture_id:06x} was not found')
                        continue
                    names = import_options(operator, tri_code, texture_id, directories, cache, folder)
                    if len(names) > 1 and choices.get(key) not in names:
                        choice = operator.texture_options.add()
                        choice.pair = key
                        choice.names = json.dumps(names)
                        choice.texture_variant = 'CHOOSE'
            if operator.texture_options:
                if bpy.app.background:
                    raise ValueError('Texture selection required for ' + ', '.join(choice.pair for choice in operator.texture_options))
                return context.window_manager.invoke_props_dialog(operator, width=850)
        # Ask before import_models can clear the current scene.
        with import_choices(choices) as warnings:
            result = operator.import_models(context)
        if warnings:
            operator.report({'WARNING'}, 'TRI fallback could not identify the requested texture version: ' + ', '.join(sorted(warnings)))
        return result
    except (ValueError, OSError, IndexError, struct.error) as error:
        operator.report({'ERROR'}, str(error))
        return {'CANCELLED'}


def candidates(texture_id, model_dir, tri_dir=None, tri_code=0):
    model_dir = Path(model_dir).resolve()
    rows = source_mapping().get(str(texture_id), [])
    selected_name = texture_name(tri_code, texture_id)
    if selected_name:
        rows = [r for r in rows if int(r[5], 16) == tri_code and r[0] == selected_name]
    region = next((p.name for p in (model_dir, *model_dir.parents) if p.name in ('us', 'eu', 'jp')), None)
    stage = next((p.name for p in (model_dir, *model_dir.parents) if any(r[1] == p.name for r in rows)), None)
    rows = [r for r in rows if (region is None or r[2] == region) and (stage is None or r[1] == stage)]
    directories = [model_dir]
    if tri_dir:
        directories.append(Path(tri_dir).resolve())
    if region:
        directories.append((model_dir / '../../tri' / region).resolve())
    from ..util.util import gv_strcode

    local_names = {}
    for path in model_dir.glob('*.tri'):
        code = int(path.stem, 16) if len(path.stem) == 6 and all(c in '0123456789abcdefABCDEF' for c in path.stem) else gv_strcode(path.stem)
        local_names.setdefault(code, []).append(path.name)
    paths = set()
    for name, source_stage, source_region, tri_name, ambiguous, code in rows:
        if Path(tri_name).name != tri_name or '/' in tri_name or '\\' in tri_name:
            continue
        if selected_name is None and (ambiguous or len(versions(int(code, 16), texture_id)) > 1):
            warn_ambiguity(texture_id)
            continue
        for directory in directories:
            if directory.name in ('us', 'eu', 'jp') and directory.name != source_region:
                continue
            names = [tri_name + '.tri']
            if directory == model_dir:
                names += local_names.get(int(code, 16), [])
            for filename in names:
                candidate = directory / filename
                if candidate.is_file():
                    paths.add(candidate)
    return sorted(paths)


def load_texture(texture_id, extract_dir, tri_dir=None, tri_code=0):
    import bpy
    from PIL import Image
    override = texture_override(tri_code, texture_id)
    if override == 'SKIP':
        return None
    custom_path = Path(override[4:]) if override else None
    name = texture_name(tri_code, texture_id)
    expected_hash = None if custom_path else texture_hashes().get(name)
    if not custom_path and len(versions(tri_code, texture_id)) > 1 and expected_hash is None:
        warn_ambiguity(texture_id)
        return None
    chosen = None
    pixels = None
    paths = [custom_path] if custom_path else candidates(texture_id, Path(extract_dir).parent, tri_dir, tri_code)
    for path in paths:
        folder = Path(extract_dir) / 'tri_fallback' / hashlib.sha256(str(path).encode()).hexdigest()[:16]
        folder.mkdir(parents=True, exist_ok=True)
        dumped = dump_texture(path, texture_id, folder)
        if dumped is None:
            continue
        with Image.open(dumped) as image:
            if expected_hash is not None and texture_hash(image) != expected_hash:
                continue
            current = (image.size, image.convert('RGBA').tobytes())
        if pixels is not None and current != pixels:
            warn_ambiguity(texture_id)
            return None
        pixels = current
        chosen = dumped
    if chosen is None:
        if custom_path:
            raise ValueError(f'{custom_path}: texture {texture_id:06x} was not found')
        if expected_hash is not None:
            warn_ambiguity(texture_id)
        return None
    image = bpy.data.images.load(chosen, check_existing=True)
    image.reload()
    image['sealouse_texture_id'] = texture_id
    if custom_path:
        image['sealouse_custom_tri'] = str(custom_path)
    print(f'Loaded {texture_id:06x} from TRI: {chosen}')
    return image
