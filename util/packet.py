from .util import getBoneIndex


def boneIndex(bone, finger_index=0):
    if 'sealouse_bone_index' in bone:
        return int(bone['sealouse_bone_index'])
    return getBoneIndex(bone.name, finger_index)


def uvMask(packet):
    return sum(1 << i for i, name in enumerate(('uvs', 'uvs2', 'uvs3')) if getattr(packet, name) is not None)


def textureFlags(packet):
    flag = 0
    if packet.colorMap:
        flag |= 0x48
    if packet.specularMap:
        flag |= 0x90
    if packet.environmentMap:
        flag |= 0x220
    return flag


def finishPacket(packet, material, draws, culled, evm=False):
    # UV presence is independent of its coordinates; (0, 0) is a valid mapping.
    required = (packet.flag >> 6) & 7
    mask = int(material.get('sealouse_uv_mask', required)) if 'flag' in material else required
    for i, name in enumerate(('uvs', 'uvs2', 'uvs3')):
        if not mask & (1 << i):
            setattr(packet, name, None)
        elif getattr(packet, name) is None and packet.vertices:
            raise ValueError(f'{material.name}: missing UV layer {i + 1}')
    # KMS stores drawing controls with normals, EVM with positions.
    records = packet.vertices if evm else packet.normals
    for record, draw in zip(records, draws):
        record.flags = draw | (0 if culled else 0x0fff)


def evmWeights(vert, packet, indices):
    from ..evm.evm import EVMWeights

    influences = sorted((indices[g.group], g.weight) for g in vert.groups if g.weight > 0)
    if not 1 <= len(influences) <= 4:
        raise ValueError('EVM vertices require one to four nonzero bone weights')
    if any(w > 1 for _, w in influences):
        raise ValueError('EVM bone weights must be between 0 and 1')
    packet.numSkin = max(packet.numSkin, len(influences))
    # truncate to bytes, then correct the largest weight so the total is 128.
    weights = [int(w * 128) for _, w in influences]
    if not any(weights):
        raise ValueError('EVM weights round down to zero; normalize them before exporting')
    largest = weights.index(max(weights))
    weights[largest] += 128 - sum(weights)
    if not 0 <= weights[largest] <= 128:
        raise ValueError('EVM weights cannot be packed; normalize them before exporting')
    return EVMWeights(weights, [packet.skinningTable.index(i) * 4 for i, _ in influences])
