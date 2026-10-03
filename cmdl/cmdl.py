from __future__ import annotations
from io import BufferedReader, BufferedWriter
from math import isnan
import struct
from ..kms.kms import KMSVector3


def float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def sourceNormal(x, y, z, renormalize=True):
    x, y, z = (float32(0.0 if isnan(c) else c) for c in (x, y, z))
    if renormalize:
        total = float32(float32(float32(x * x) + float32(y * y)) + float32(z * z))
        length = float32(total**0.5)
        if length:
            x, y, z = (float32(c / length) for c in (x, y, z))
    return x, y, z


def packedNormalComponents(x, y, z, renormalize=True):
    normal = sourceNormal(x, y, z, renormalize)
    # int() matches c++ uint32 cast, truncating instead of rounding.
    return tuple(int(float32(c * scale)) for c, scale in zip(normal, (1023, 1023, 511)))


class CMDL:
    header: CMDLHeader
    sections: List[CMDLSection]
    tail: CMDLTail
    
    def __init__(self):
        self.header = CMDLHeader()
        self.sections = []
        self.tail = CMDLTail()
    
    def fromFile(self, file: BufferedReader):
        self.header.fromFile(file)
        
        self.sections = [
            CMDLSection().fromFile(file)
            for _ in range(self.header.numSection)
        ]
        
        file.seek(self.header.tailOffset + 0xC)
        self.tail.fromFile(file)
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        self.header.numSection = len(self.sections)
        self.tail.numFaces = len(self.tail.faces)
        curSectionOffset = 0x10 + 0x20 * len(self.sections)
        file.seek(curSectionOffset)
        file.write(struct.pack("<3i", -1, -1, -1))
        for section_index, section in enumerate(self.sections):
            section.dataSize = section.data.size * len(section.data.data)
            section.dataOffset = curSectionOffset
            curSectionOffset += section.dataSize
            if section_index + 1 < len(self.sections) and curSectionOffset % 0x10 > 0:
                file.seek(curSectionOffset + 0xC)
                while ((file.tell() % 0x10) & 0xC) != 0xC:
                    file.write(struct.pack("<i", -1))
                curSectionOffset = file.tell() - 0xC
        
        self.header.tailOffset = curSectionOffset
        
        file.seek(0)
        
        self.header.writeToFile(file)
        
        for section in self.sections:
            section.writeToFile(file)
        
        file.seek(self.header.tailOffset + 0xC)
        self.tail.writeToFile(file)
            
        return


class CMDLHeader:
    magic: bytes
    version: int
    tailOffset: int
    numSection: int
    
    def __init__(self):
        self.magic = b"MODL"
        self.version = 1
        self.tailOffset = 0
        self.numSection = 0
    
    def fromFile(self, file: BufferedReader):
        self.magic = file.read(4)
        assert(self.magic == b"MODL") # Unexpected header magic
        self.version, self.tailOffset = struct.unpack(">II", file.read(8))
        assert(self.version == 1) # Unexpected header version
        self.numSection = struct.unpack("<I", file.read(4))[0]
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        file.write(self.magic)
        file.write(struct.pack(">II", self.version, self.tailOffset))
        file.write(struct.pack("<I", self.numSection))


class CMDLSection:
    magic: bytes
    storageType: int  # vertex attribute storage type
    flags: int  # 2: dataOffset is relative to the vertex array
    dataOffset: int
    dataSize: int
    data: CMDLSectionData
    
    def __init__(self, magic="xxxx"):
        if type(magic) is str:
            self.magic = bytes(magic, "utf-8")
        else:
            self.magic = bytes(magic)
        self.storageType = 0
        self.flags = 2
        self.dataOffset = 0
        self.dataSize = 0
        if self.magic == b"POS0":
            self.data = CMDLPosData()
            self.storageType = 2
        elif self.magic == b"BONI":
            self.data = CMDLBonIData()
            self.storageType = 4
        elif self.magic[:3] == b"TEX":
            self.data = CMDLTexData()
            self.storageType = 6 if self.magic == b"TEX2" else 5
            self.data.size = 8 if self.magic == b"TEX2" else 4
        elif self.magic == b"BONW":
            self.data = CMDLBonWData()
            self.storageType = 6
        elif self.magic == b"NRM0":
            self.data = CMDLNrmData()
            self.storageType = 9
        elif self.magic == b"OIDX":
            self.data = CMDLOIdxData()
            self.storageType = 0xA
        else:
            self.data = None
    
    def fromFile(self, file: BufferedReader):
        self.magic = bytes(reversed(file.read(4)))
        if not (self.magic in { b"POS0", b"NRM0", b"OIDX", b"BONI", b"BONW" } or self.magic[:3] == b"TEX"):
            raise Exception(f"Unexpected section magic {self.magic}")
        self.storageType, self.flags, self.dataOffset, pad \
        = struct.unpack("<HHII", file.read(0xC))
        assert(pad == 0) # Expected zero
        self.dataSize, pad1, pad2, pad3 = struct.unpack("<IIII", file.read(0x10))
        assert(pad1 == 0 and pad2 == 0 and pad3 == 0) # Expected zero
        
        if self.magic == b"POS0":
            self.data = CMDLPosData()
        elif self.magic == b"NRM0":
            self.data = CMDLNrmData()
        elif self.magic == b"OIDX":
            self.data = CMDLOIdxData()
        elif self.magic == b"BONI":
            self.data = CMDLBonIData()
        elif self.magic == b"BONW":
            self.data = CMDLBonWData()
        elif self.magic[:3] == b"TEX":
            self.data = CMDLTexData()
            if self.storageType == 6:
                self.data.size = 8
        else:
            assert(False) # How did we get here?
        
        curPos = file.tell()
        file.seek(self.dataOffset + 0xC)
        self.data.fromFile(file, self.dataSize)
        file.seek(curPos)
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        file.write(bytes(reversed(self.magic)))
        file.write(struct.pack("<HHIIIIII", self.storageType, self.flags, self.dataOffset, 0, \
        self.dataSize, 0, 0, 0))
        
        curPos = file.tell()
        file.seek(self.dataOffset + 0xC)
        self.data.writeToFile(file)
        file.seek(curPos)


class CMDLSectionData:
    data: List[any]
    size: int
    
    def __init__(self):
        self.data = []
    def fromFile(self, file: BufferedReader):
        assert(False) # Attempt to read an abstract class
    def writeToFile(self, file: BufferedWriter):
        assert(False) # Attempt to write an abstract class

class CMDLPosData(CMDLSectionData): # Coordinates
    size = 0x10
    
    def __init__(self):
        self.data = []
    
    def fromFile(self, file: BufferedReader, fullSize: int):
        vertCount = fullSize // self.size
        self.data = [struct.unpack("<ffff", file.read(0x10)) for _ in range(vertCount)]
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        for vert in self.data:
            file.write(struct.pack("<ffff", vert[0], vert[1], vert[2], vert[3]))

class CMDLNrmData(CMDLSectionData): # Normals
    size = 4
    
    def __init__(self):
        self.data = []
        self.renormalize = True
    
    def fromFile(self, file: BufferedReader, fullSize: int):
        vertCount = fullSize // self.size
        for _ in range(vertCount):
            # I would like to express my profound gratitude to... I forget where I found this.
            # Either WoefulWolf's Nier2Blender2Nier, Kerilk's bayonetta_tools, or I wrote it myself based on both
            # 11-bit x, 11-bit y, 10-bit z
            normal = struct.unpack("<I", file.read(4))[0]
            
            normalX = normal & ((1 << 11) - 1)
            normalY = (normal >> 11) & ((1 << 11) - 1)
            normalZ = (normal >> 22)
            # sign bits
            if normalX & (1 << 10):
                normalX &= ~(1 << 10)
                normalX -= 1 << 10
            if normalY & (1 << 10):
                normalY &= ~(1 << 10)
                normalY -= 1 << 10
            if normalZ & (1 << 9):
                normalZ &= ~(1 << 9)
                normalZ -= 1 << 9
            # keep the decoded normal's original length.
            normalX /= (1<<10)-1
            normalY /= (1<<10)-1
            normalZ /= (1<<9)-1
            
            self.data.append((normalX, normalY, normalZ))
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        for vert in self.data:
            nx, ny, nz = packedNormalComponents(*vert, self.renormalize)
            normal = (nx & 2047) | ((ny & 2047) << 11) | ((nz & 1023) << 22)
            file.write(struct.pack("<I", normal))

class CMDLTexData(CMDLSectionData): # UV maps
    size = 4
    
    def __init__(self):
        self.data = []
    
    def fromFile(self, file: BufferedReader, fullSize: int):
        vertCount = fullSize // self.size
        # Everybody loves the half-precision float format (5 bit exponent, 10 bit mantissa)
        self.data = [struct.unpack("<" + "e" * (self.size // 2), file.read(self.size)) for _ in range(vertCount)]
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        for vert in self.data:
            file.write(struct.pack("<" + "e" * (self.size // 2), *vert))

class CMDLOIdxData(CMDLSectionData):  # indices into the flattened KMS/EVM vertex stream
    size = 4
    
    def __init__(self):
        self.data = []
    
    def fromFile(self, file: BufferedReader, fullSize: int):
        vertCount = fullSize // self.size
        self.data = [struct.unpack("<I", file.read(4))[0] for _ in range(vertCount)]
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        for vert in self.data:
            file.write(struct.pack("<I", vert))

class CMDLBonIData(CMDLSectionData): # Bone Indexes (EVM)
    size = 4
    
    def __init__(self):
        self.data = []
    
    def fromFile(self, file: BufferedReader, fullSize: int):
        vertCount = fullSize // self.size
        self.data = [list(struct.unpack("<4B", file.read(4))) for _ in range(vertCount)]
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        for vert in self.data:
            for boneIndex in vert:
                file.write(struct.pack("<B", boneIndex))

class CMDLBonWData(CMDLSectionData): # Bone weights (EVM)
    size = 8
    
    def __init__(self):
        self.data = []
    
    def fromFile(self, file: BufferedReader, fullSize: int):
        vertCount = fullSize // self.size
        # Everybody loves the half-precision float format (5 bit exponent, 10 bit mantissa)
        self.data = [list(struct.unpack("<4e", file.read(8))) for _ in range(vertCount)]
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        for vert in self.data:
            for boneWeight in vert:
                file.write(struct.pack("<e", boneWeight))


class CMDLTail:
    numFaces: int
    faces: List[List[int]]
    numMeshes: int
    meshes: List[CMDLMesh]
    
    def __init__(self):
        self.numFaces = 0
        self.faces = []
        self.numMeshes = 0
        self.meshes = []
    
    def fromFile(self, file: BufferedReader):
        numFaceIndexes = struct.unpack(">I", file.read(4))[0]
        assert(numFaceIndexes % 3 == 0) # Unexpected face index count
        self.numFaces = numFaceIndexes // 3
        self.faces = [struct.unpack(">III", file.read(0xC)) for _ in range(self.numFaces)]
        pad, self.numMeshes = struct.unpack(">II", file.read(8))
        assert(pad == 0) # Expected zero
        self.meshes = [
            CMDLMesh().fromFile(file)
            for _ in range(self.numMeshes)
        ]
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        file.write(struct.pack(">I", self.numFaces * 3))
        for face in self.faces:
            file.write(struct.pack(">III", face[0], face[1], face[2]))
        
        file.write(struct.pack(">II", 0, self.numMeshes))
        for mesh in self.meshes:
            mesh.writeToFile(file)
        
class CMDLMesh:
    minPos: KMSVector3
    maxPos: KMSVector3
    unknown_18: int # two bytes, -1
    unknown_1C: int # one byte
    startVertex: int
    vertexCount: int
    startFace: int
    faceCount: int # x3
    bones: List[int]
    boneCount: int # always zero for KMS
    unknown_2E: bytes # 8 bytes, all 0x80
    unknown_36: int # four bytes
    meshIndex: int
    subMeshIndex: int
    
    def __init__(self):
        self.minPos = KMSVector3()
        self.maxPos = KMSVector3()
        self.unknown_18 = -1
        self.unknown_1C = 0
        self.startVertex = 0
        self.vertexCount = 0
        self.startFace = 0
        self.faceCount = 0
        self.boneCount = 0
        self.bones = []
        self.unknown_2D = 0
        self.unknown_2E = b"\x80\x80\x80\x80\x80\x80\x80\x80"
        self.unknown_36 = 0
        self.meshIndex = 0
        self.subMeshIndex = 0
    
    def fromFile(self, file: BufferedReader):
        self.minPos.fromFile(file, True)
        self.maxPos.fromFile(file, True)
        self.unknown_18, self.unknown_1C, \
        self.startVertex, self.vertexCount, self.startFace, self.faceCount, \
        self.boneCount = struct.unpack(">hBIIIII", file.read(23)) # this BS doesn't deserve hexadecimal
        assert(self.unknown_18 == -1) # Expected -1
        assert(self.unknown_1C == 0) # Expected 0
        for i in range(self.boneCount):
            self.bones.append(struct.unpack(">I", file.read(4))[0])
        self.unknown_2E = file.read(8)
        assert(self.unknown_2E == b"\x80\x80\x80\x80\x80\x80\x80\x80") # Expected... whatever this is
        self.unknown_36, self.meshIndex, self.subMeshIndex \
        = struct.unpack(">III", file.read(0xC))
        assert(self.unknown_36 == 0) # Expected 0
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        self.minPos.writeToFile(file, True)
        self.maxPos.writeToFile(file, True)
        file.write(struct.pack(">hBIIIII", self.unknown_18, self.unknown_1C, \
        self.startVertex, self.vertexCount, self.startFace, self.faceCount, \
        self.boneCount))
        for bone in self.bones:
            file.write(struct.pack(">I", bone))
        file.write(self.unknown_2E)
        file.write(struct.pack(">III", self.unknown_36, self.meshIndex, self.subMeshIndex))
