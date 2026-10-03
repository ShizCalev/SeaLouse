# Track imported model data and mesh edits.

import base64
import hashlib
import json
import struct
import zlib
from ..kms import kms
from ..evm import evm
from .util import _normalState
from .materials import TextureSave

KEY = 'sealouse_native_state_v1'


def originalModel(collection):
    archived = collection.get(KEY)
    return decode(json.loads(zlib.decompress(base64.b64decode(archived)))['model']) if archived else None


def encode(value):
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    if hasattr(value, '__dict__'):
        return {
            'type': type(value).__name__,
            'fields': {k: encode(v) for k, v in vars(value).items() if k != 'parent'},
        }
    return value


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if 'bytes' in value:
            return bytes.fromhex(value['bytes'])
        cls = getattr(kms, value['type'], None) or getattr(evm, value['type'])
        obj = cls()
        for k, v in value['fields'].items():
            # older .blend files cached these fields under their previous names.
            if cls is evm.EVMUv and k == 'unknown':
                obj.z, obj.w = struct.unpack('<hh', struct.pack('<I', v))
                continue
            if cls is evm.EVMBone and k == 'pad':
                k = 'flag'
            setattr(obj, k, decode(v))
        return obj
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def mesh_state(obj):
    mesh = obj.data
    tex = TextureSave()
    extra = {}
    if 'sealouse_mesh_index' in obj:
        extra['mesh_index'] = obj['sealouse_mesh_index']
    if any(s.material and 'sealouse_uv_mask' in s.material for s in obj.material_slots):
        extra['uv_masks'] = [s.material.get('sealouse_uv_mask') if s.material else None for s in obj.material_slots]
    return digest({
        'geometry': _normalState(mesh),
        'uv': [[list(u.vector) for u in layer.uv] for layer in mesh.uv_layers],
        'material_indices': [p.material_index for p in mesh.polygons],
        'materials': [
            (m.material.get('flag'), *[tex.get_map(m.material, k) for k in ('colorMap', 'specularMap', 'environmentMap')])
            if m.material else None
            for m in obj.material_slots
        ],
        'weights': [[(g.group, g.weight) for g in v.groups] for v in mesh.vertices],
        'groups': [g.name for g in obj.vertex_groups],
        'transform': [list(r) for r in obj.matrix_basis],
        'parent_inverse': [list(r) for r in obj.matrix_parent_inverse],
        'flag': obj.get('flag'),
        **extra,
        **({'attachment_parent': obj['kms_attachment_parent']} if 'kms_attachment_parent' in obj else {}),
        'modifiers': [(m.type, m.show_viewport, m.show_render) for m in obj.modifiers],
    })


def arm_state(arm):
    metadata = [(b.get('sealouse_bone_index'), b.get('sealouse_bone_flag'), b.get('sealouse_runtime_bone')) for b in arm.data.bones]
    # leave older .blend cache signatures unchanged when no new metadata is present.
    extra = {'bone_metadata': metadata} if any(v is not None for row in metadata for v in row) else {}
    return digest({
        'transform': [list(r) for r in arm.matrix_basis],
        'bones': [(b.name, b.parent.name if b.parent else None, list(b.head_local), list(b.tail_local)) for b in arm.data.bones],
        **extra,
        'props': {
            k: list(arm[k]) if k.startswith('bbox') else arm.get(k)
            for k in ('bboxMin', 'bboxMax', 'kmsType', 'strcode', 'flag', 'fingerIndex') if k in arm
        },
    })


def capture(collection, model):
    meshes = [o for o in collection.all_objects if o.type == 'MESH']
    arm = next(o for o in collection.all_objects if o.type == 'ARMATURE')
    for o in meshes:
        o['sealouse_import_local_matrix'] = [c for row in (o.matrix_parent_inverse @ o.matrix_basis) for c in row]
    state = {
        'model': encode(model),
        'arm': arm_state(arm),
        'meshes': [(o.name, mesh_state(o)) for o in meshes],
    }
    collection[KEY] = base64.b64encode(zlib.compress(json.dumps(state, separators=(',', ':')).encode())).decode()


def restore(collection, model):
    archived = collection.get(KEY)
    if not archived:
        return model
    state = json.loads(zlib.decompress(base64.b64decode(archived)))
    meshes = [o for o in collection.all_objects if o.type == 'MESH']
    arm = next(o for o in collection.all_objects if o.type == 'ARMATURE')
    if arm_state(arm) != state['arm']:
        return model
    if any(m.type != 'ARMATURE' and m.show_viewport for o in meshes for m in o.modifiers):
        return model
    current = [(o.name, mesh_state(o)) for o in meshes]
    previous = [tuple(x) for x in state['meshes']]
    original = decode(state['model'])
    if isinstance(original, kms.KMS) and original.header.isPs2:
        original.header.pad = 0
        original.header.isPs2 = False
        for mesh in original.meshes:
            mesh.pad = bytes(0x1C)
            for packet in mesh.vertexGroups:
                packet.pad8 = bytes(0x1C)
    if current == previous:
        return original
    if isinstance(model, kms.KMS) and [x[0] for x in current] == [x[0] for x in previous]:
        for i, (a, b) in enumerate(zip(current, previous)):
            if a == b:
                model.meshes[i] = original.meshes[i]
    return model
