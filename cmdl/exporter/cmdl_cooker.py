import struct
from ..cmdl import CMDL, CMDLSection, CMDLMesh, float32, sourceNormal
from ...evm.evm import evmPosScale


def position_hash(p):
    a, b, c, d = struct.unpack('<4I', struct.pack('<4f', *p))
    h = (a + 11 * b - 17 * c + 20 * d) & 0x7FFFFFFF
    return (h >> 22) ^ (h >> 12) ^ h


def equal(a, b):
    if isinstance(a, tuple):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return abs(float32(a - b)) < 2**-23


def dot(a, b):
    return float32(float32(float32(a[0] * b[0]) + float32(a[1] * b[1])) + float32(a[2] * b[2]))


def subtract(a, b):
    return tuple(float32(x - y) for x, y in zip(a[:3], b[:3]))


def cross(a, b):
    return tuple(float32(float32(a[j] * b[k]) - float32(a[k] * b[j])) for j, k in ((1, 2), (2, 0), (0, 1)))


def strip_triangles(packet, evm=False):
    # advance the strip even when this vertex doesn't draw a triangle.
    positions = [(v.x, v.y, v.z) for v in packet.vertices]
    a, b, c, clock = 0, 1, 2, True
    for i in range(2, len(positions)):
        if (packet.vertices if evm else packet.normals)[i].isFace:
            n = packet.normals[i]
            normal = (n.x / 4096, n.y / 4096, n.z / 4096)
            edge1 = subtract(positions[b], positions[a])
            edge2 = subtract(positions[c], positions[a])
            yield (a, c, b) if dot(normal, cross(edge1, edge2)) <= 0 else (a, b, c)
        if clock:
            a = c
        else:
            b = c
        clock = not clock
        c += 1


def optimize(triangles, vertex_count):
    # match bp's triangle order, including ties.
    if not triangles:
        return []
    adjacent = [[] for _ in range(vertex_count)]
    for i, tri in enumerate(triangles):
        for v in tri:
            adjacent[v].append(i)
    active = list(map(len, adjacent))
    tags = [-1] * vertex_count

    def score(v):
        if not active[v]:
            return -1.0
        s = 0.0
        p = tags[v]
        if p >= 0:
            s = (0.75 if p < 3 else float32(float32(1 - float32((p - 3) * float32(1 / 29))) ** 1.5))
        return float32(s + float32(2 * float32(active[v] ** -0.5)))

    scores = [score(v) for v in range(vertex_count)]

    def tri_score(tri):
        return float32(float32(scores[tri[0]] + scores[tri[1]]) + scores[tri[2]])

    ts = [tri_score(t) for t in triangles]
    used = [False] * len(triangles)
    cache = [0] * 35
    best = -1
    best_score = -2.0
    output = []
    for _ in triangles:
        if best_score < float32(0.1):
            for i, s in enumerate(ts):
                if not used[i] and best_score < s:
                    best = i
                    best_score = s
        tri = triangles[best]
        used[best] = True
        ts[best] = -1.0
        output.append(tri)
        for v in tri:
            active[v] -= 1
            if tags[v] != -1:
                cache.pop(tags[v])
            cache.insert(0, v)
        for i, v in enumerate(cache):
            tags[v] = i
        for i, v in enumerate(cache):
            tags[v] = i if i < 32 else -1
            scores[v] = score(v)
        best = -1
        best_score = -2.0
        for v in cache:
            for i in adjacent[v]:
                if used[i]:
                    continue
                ts[i] = tri_score(triangles[i])
                if best_score < ts[i]:
                    best = i
                    best_score = ts[i]
        del cache[32:]
    return output


def cook(model, evm=False):
    packets = (
        [(0, i, p) for i, p in enumerate(model.meshes)] if evm
        else [(i, j, p) for i, m in enumerate(model.meshes) for j, p in enumerate(m.vertexGroups)]
    )
    has_uv = [any(getattr(p, name) is not None for _, _, p in packets) for name in ('uvs', 'uvs2', 'uvs3')]
    result = CMDL()
    sections = {}
    for tag in (
        [b'POS0', b'NRM0']
        + [b'TEX' + bytes([48 + i]) for i, h in enumerate(has_uv) if h]
        + ([b'BONI', b'BONW'] if evm else [])
        + [b'OIDX']
    ):
        s = CMDLSection(tag)
        sections[tag] = s
        result.sections.append(s)
    sections[b'NRM0'].data.renormalize = False
    scale = evmPosScale(model.header.flag) if evm else 1
    offset = 0
    for mi, pi, p in packets:
        positions = [(v.x * scale, v.y * scale, v.z * scale, 1.0 if evm else v.weight / 4096) for v in p.vertices]
        normals = [sourceNormal(n.x / 4096, n.y / 4096, n.z / 4096, not evm) for n in p.normals]
        if not evm:
            # zero normals become NaNs during KMS conversion, so these vertices stay separate.
            normals = [(float('nan'),) * 3 if (n.x, n.y, n.z) == (0, 0, 0) else normal for n, normal in zip(p.normals, normals)]
        uvs = []
        for k, name in enumerate(('uvs', 'uvs2', 'uvs3')):
            layer = getattr(p, name)
            uvs.append([
                (u.u / 4096, u.v / 4096, u.z / 4096, u.w / 4096)
                if evm else (u.u / 4096, u.v / 4096, 0.0, 0.0)
                for u in layer
            ] if layer is not None else [(0.0,) * 4] * len(positions))
        weights = []
        for i in range(len(positions)):
            ws = []
            if evm and p.weights is not None:
                w = p.weights[i]
                ws = [(p.skinningTable[w.indices[j] // 4], w.weights[j] / 128) for j in range(p.numSkin) if w.weights[j] > 0]
            weights.append(tuple(sorted(ws, key=lambda pair: -pair[1])))
        triangles = []
        a, b, c = 0, 1, 2
        clk = True
        for i in range(2, len(positions)):
            if (p.vertices if evm else p.normals)[i].isFace:
                tri = (a, b, c)
                n = p.normals[i]
                raw_n = (n.x / 4096, n.y / 4096, n.z / 4096)
                if dot(raw_n, cross(subtract(positions[b], positions[a]), subtract(positions[c], positions[a]))) <= 0:
                    tri = (a, c, b)
                # check both edges from the first corner before merging vertices.
                if all(
                    float32(dot(d, d) ** 0.5) >= 2**-23
                    for d in (subtract(positions[tri[1]], positions[tri[0]]), subtract(positions[tri[2]], positions[tri[0]]))
                ):
                    triangles.append(tri)
            if clk:
                a = c
            else:
                b = c
            clk = not clk
            c += 1
        bones = sorted({j for tri in triangles for i in tri for j, w in weights[i]})
        base = len(sections[b'POS0'].data.data)
        refs = []
        seen = {}
        faces = []
        for tri in triangles:
            face = []
            for i in tri:
                attrs = (positions[i], normals[i], tuple(uvs[k][i] for k, h in enumerate(has_uv) if h), weights[i])
                bucket = seen.setdefault(position_hash(positions[i]), [])
                idx = next((idx for old, idx in bucket if equal(attrs, old)), None)
                if idx is None:
                    idx = len(refs)
                    refs.append(i)
                    bucket.append((attrs, idx))
                face.append(idx)
            faces.append(tuple(face))
        chunk = CMDLMesh()
        chunk.meshIndex = mi
        chunk.subMeshIndex = pi
        chunk.startVertex = base
        chunk.vertexCount = len(refs)
        chunk.startFace = len(result.tail.faces) * 3 if faces else 0
        chunk.faceCount = len(faces) * 3
        chunk.bones = bones
        chunk.boneCount = len(bones)
        if refs:
            chunk.minPos.set(tuple(min(positions[i][k] for i in refs) for k in range(3)))
            chunk.maxPos.set(tuple(max(positions[i][k] for i in refs) for k in range(3)))
        else:
            # empty chunks retain bp's initial inverted bounds.
            largest = float.fromhex('0x1.fffffep+127')
            chunk.minPos.set((largest,) * 3)
            chunk.maxPos.set((-largest,) * 3)
        result.tail.meshes.append(chunk)
        ordered = optimize(faces, len(refs))
        remap = {}
        for tri in ordered:
            for v in tri:
                if v not in remap:
                    remap[v] = len(remap)
        refs = [refs[v] for v in remap]
        result.tail.faces.extend(tuple(base + remap[v] for v in tri) for tri in ordered)
        for i in refs:
            sections[b'POS0'].data.data.append(positions[i])
            sections[b'NRM0'].data.data.append(normals[i])
            sections[b'OIDX'].data.data.append(offset + i)
            for k, h in enumerate(has_uv):
                if h:
                    sections[b'TEX' + bytes([48 + k])].data.data.append(uvs[k][i] if k == 2 else uvs[k][i][:2])
            if evm:
                ws = weights[i]
                sections[b'BONI'].data.data.append([bones.index(j) for j, w in ws] + [0] * (4 - len(ws)))
                sections[b'BONW'].data.data.append([w for j, w in ws] + [0.0] * (4 - len(ws)))
        offset += len(positions)
    result.tail.numMeshes = len(result.tail.meshes)
    return result
