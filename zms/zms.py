import copy
import io
import struct
from ..kms.kms import KMS


class ZMS:
    def __init__(self):
        self.members = []

    def fromFile(self, file):
        data = file.read()
        if len(data) < 16:
            raise ValueError("Truncated ZMS header")
        magic, version, flags, count = struct.unpack_from('<4I', data)
        if (magic, version, flags) != (113171, 0, 0) or not 0 < count <= (len(data) - 16) // 16:
            raise ValueError("Unsupported ZMS header")
        self.members = []
        for i in range(count):
            offset, word1, word2, word3 = struct.unpack_from('<4I', data, 16 + i * 16)
            if offset < 16 + count * 16 or offset + 64 > len(data):
                raise ValueError("Invalid ZMS member offset")
            if struct.unpack_from('<I', data, offset)[0] != 11686819:
                raise ValueError("Unsupported KMS format in ZMS archive")
            mesh_count, ps2 = struct.unpack_from('<iI', data, offset + 8)
            mesh_size, packet_size = (64, 48) if ps2 else (80, 96)
            if mesh_count < 0 or offset + 64 + mesh_count * mesh_size > len(data):
                raise ValueError("Invalid ZMS mesh count")
            for m in range(mesh_count):
                mesh_offset = offset + 64 + m * mesh_size
                packet_count = struct.unpack_from('<I', data, mesh_offset + 4)[0]
                packet_offset = struct.unpack_from('<I', data, mesh_offset + 48)[0]
                if packet_offset + packet_count * packet_size > len(data):
                    raise ValueError("Invalid ZMS packet range")
                for p in range(packet_count):
                    start = packet_offset + p * packet_size
                    vertex_count = struct.unpack_from('<I', data, start + 4)[0]
                    fields = (20, 24, 28, 32, 36) if ps2 else (32, 40, 48, 56, 64)
                    for field, stride in zip(fields, (8, 8, 4, 4, 4)):
                        address = struct.unpack_from('<I', data, start + field)[0]
                        if (address or stride == 8) and (address < 16 + count * 16 or address + vertex_count * stride > len(data)):
                            raise ValueError("Invalid ZMS vertex attribute range")
            stream = io.BytesIO(data)
            stream.seek(offset)
            model = KMS().fromFile(stream)
            # x64 widened from +4 to +8
            ident = word1 if model.header.isPs2 else word2
            if word3 or (word2 if model.header.isPs2 else word1):
                raise ValueError("Unsupported ZMS member table")
            if ident in [item[0] for item in self.members]:
                raise ValueError("Duplicate ZMS member ID")
            self.members.append((ident, model))
        return self

    def writeToFile(self, file):
        members = copy.deepcopy(self.members)
        if not members or len({ident for ident, model in members}) != len(members):
            raise ValueError("ZMS needs members with unique IDs")
        offset = 16 + 16 * len(members)
        entries = []
        groups = []
        for ident, model in members:
            entries.append((offset, 0, ident, 0))
            model.header.pad = 0
            model.header.isPs2 = False
            model.header.numMesh = len(model.meshes)
            offset += 64 + 80 * len(model.meshes)
            packets = []
            for mesh in model.meshes:
                mesh.pad = bytes(28)
                mesh.numVertexGroup = len(mesh.vertexGroups)
                mesh.vertexGroupOffset = offset
                offset += 96 * mesh.numVertexGroup
                packets.extend(mesh.vertexGroups)
            groups.append(packets)

        banks = []
        for attr, pointer in [('vertices', 'vertexOffset'), ('normals', 'normalOffset')]:
            previous = {}
            bank = bytearray()
            for packets in groups:
                pending = {}
                for packet in packets:
                    packet.numVertex = len(packet.vertices)
                    values = getattr(packet, attr)
                    if values is None:
                        setattr(packet, pointer, 0)
                        continue
                    if len(values) != packet.numVertex:
                        raise ValueError("ZMS vertex attribute counts do not match")
                    stream = io.BytesIO()
                    for value in values:
                        value.writeToFile(stream)
                    raw = stream.getvalue()
                    raw += bytes(-len(raw) % 16)
                    key = (packet.numVertex, raw)
                    address = previous.get(key)
                    if address is None:
                        address = offset + len(bank)
                        bank.extend(raw)
                    setattr(packet, pointer, address)
                    pending.setdefault(key, address)
                # Only earlier members participate in the archiver's vertex/normal sharing.
                for key, address in pending.items():
                    previous.setdefault(key, address)
            banks.append(bank)
            offset += len(bank)

        bank = bytearray()
        for packets in groups:
            for attr, pointer in [('uvs', 'uvOffset'), ('uvs2', 'uv2Offset'), ('uvs3', 'uv3Offset')]:
                for packet in packets:
                    values = getattr(packet, attr)
                    if values is None:
                        setattr(packet, pointer, 0)
                        continue
                    if len(values) != packet.numVertex:
                        raise ValueError("ZMS UV count does not match vertex count")
                    setattr(packet, pointer, offset + len(bank))
                    stream = io.BytesIO()
                    for value in values:
                        value.writeToFile(stream)
                    raw = stream.getvalue()
                    bank.extend(raw + bytes(-len(raw) % 16))
        banks.append(bank)

        file.write(struct.pack('<4I', 113171, 0, 0, len(members)))
        for entry in entries:
            file.write(struct.pack('<4I', *entry))
        for (ident, model), packets in zip(members, groups):
            model.header.writeToFile(file)
            for mesh in model.meshes:
                mesh.writeToFile(file)
            for packet in packets:
                packet.writeToFile(file)
        for bank in banks:
            file.write(bank)
