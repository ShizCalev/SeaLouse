from __future__ import annotations
from io import BufferedReader, BufferedWriter
import struct
import copy
import hashlib
from os import path


# afevis fix from bp's original ctxr cooker: correct hasAlpha on every export using PS2's 128-is-opaque range; false disables the fix.
HAS_ALPHA_USE_128_THRESHOLD = True


class CTXR:
    header: CTXRHeader
    chunks: list[CTXRChunk]
    
    def __init__(self):
        self.header = CTXRHeader()
        self.chunks = []
    
    def fromFile(self, file: BufferedReader):
        self.header.fromFile(file)
        
        self.chunks = [
            CTXRChunk().fromFile(file)
            for _ in range(self.header.numMipmaps)
        ]
        
        return self
    
    def convertDDS(self) -> DDS:
        if self.header.version != 7 or self.header.format != 0 or self.header.type != 0 or self.header.depth != 1:
            raise ValueError("DDS extraction currently supports only MC A8R8G8B8 CTXR textures")
            # user tried to open a ps3/switch ctxr
        dds = DDS()
        dds.header.flags = 0x2100f
        dds.header.width = self.header.width
        dds.header.height = self.header.height
        dds.header.pitch = 0
        dds.header.numMipmaps = self.header.numMipmaps
        dds.header.pixelFormat.flags = 0x41
        dds.header.caps[0] = 0x1000
        if self.header.numMipmaps > 0:
            dds.header.caps[0] |= 0x400008
        for chunk in self.chunks:
            dds.data += chunk.data
        return dds
    
    def writeToFile(self, file: BufferedWriter):
        self.header.writeToFile(file)
        for chunk in self.chunks:
            chunk.writeToFile(file)

#header ported from https://github.com/316austin316/CTXR-Converter/blob/main/ctxr_utils.py
class CTXRHeader:
    magic: bytes  # "TXTR"
    version: int  # 7
    width: int
    height: int
    depth: int
    format: int
    hasAlpha: int
    additionalFlags: int
    minRGBA: int
    maxRGBA: int
    filterHint: int
    alphaRefValue: int
    maxLODOffset: int
    type: int
    numMipmaps: int
    padding: bytes
    
    def __init__(self):
        self.magic = b"TXTR"
        self.version = 7
        self.width = 0
        self.height = 0
        self.depth = 1
        self.format = 0
        self.hasAlpha = 0
        self.additionalFlags = 0
        self.minRGBA = 0
        self.maxRGBA = 0
        self.filterHint = -1
        self.alphaRefValue = 0xff
        self.maxLODOffset = 0
        self.type = 0
        self.numMipmaps = 1
        self.padding = bytes(89)
    
    def fromFile(self, file: BufferedReader):
        # filter and lod hints are signed bytes.
        self.magic, self.version, self.width, self.height, self.depth, \
        self.format, self.hasAlpha, self.additionalFlags, self.minRGBA, self.maxRGBA, \
        self.filterHint, self.alphaRefValue, self.maxLODOffset, self.type, self.numMipmaps \
        = struct.unpack(">4sIHHHIBIIIbBbIB", file.read(0x27))
        if self.magic != b'TXTR' or self.version != 7:
            raise ValueError("Expected a v7 TXTR header")
        self.padding = file.read(89)
        if len(self.padding) != 89:
            raise ValueError("Truncated CTXR header")
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        file.write(struct.pack(">4sIHHHIBIIIbBbIB", self.magic, self.version, \
        self.width, self.height, self.depth, self.format, self.hasAlpha, \
        self.additionalFlags, self.minRGBA, self.maxRGBA, self.filterHint, \
        self.alphaRefValue, self.maxLODOffset, self.type, self.numMipmaps))
        file.write(self.padding)

class CTXRChunk:
    size: int
    data: bytes
    
    def __init__(self):
        self.size = 0
        self.data = b""
    
    def fromFile(self, file: BufferedReader):
        self.size = struct.unpack(">I", file.read(4))[0]
        self.data = file.read(self.size)
        while file.tell() % 0x20 != 0:
            file.read(1)
    
        return self
    
    def writeToFile(self, file: BufferedWriter):
        file.write(struct.pack(">I", self.size))
        file.write(self.data)
        while file.tell() % 0x20 != 0:
            file.write(b"\0")


class DDS:
    header: DDSHeader
    data: bytes
    
    def __init__(self):
        self.header = DDSHeader()
        self.data = b""
    
    def fromFile(self, file: BufferedReader):
        self.header.fromFile(file)
        
        self.data = file.read()
        
        return self
    
    def contentDigest(self):
        dimensions = struct.pack('<III', self.header.width, self.header.height, self.header.numMipmaps)
        return hashlib.sha256(dimensions + self.data).hexdigest()

    def convertCTXR(self, source_header=None, source_digest=None) -> CTXR:
        if self.header.pixelFormat.fourcc or self.header.pixelFormat.bitCount != 32 or self.header.pixelFormat.bitMasks != [0xff0000, 0xff00, 0xff, 0xff000000]:
            raise ValueError("CTXR packing requires uncompressed 32-bit BGRA DDS")
        if self.header.depth > 1 or self.header.caps[1]:
            raise ValueError("CTXR packing requires a 2D DDS, not a volume or cubemap")
        if self.header.width < 1 or self.header.height < 1 or self.header.numMipmaps < 1:
            raise ValueError("DDS dimensions and mip count must be positive")
        ctxr = CTXR()
        if source_header is not None:
            if source_header.format != 0 or source_header.type != 0 or source_header.depth != 1:
                raise ValueError("Cannot replace a non-BGRA, volume or cubemap CTXR with a 2D DDS")
            ctxr.header = copy.deepcopy(source_header)
        ctxr.header.width = self.header.width
        ctxr.header.height = self.header.height
        # afevis fix from vanilla ctxr cooker: use the DDS mip count; the vanilla cooker's ceil(log2(size)) overcounts NPOT chains.
        ctxr.header.numMipmaps = self.header.numMipmaps
        ctxr.chunks = []
        
        dataPos = 0
        width, height = self.header.width, self.header.height
        for i in range(self.header.numMipmaps):
            dataSize = width * height * 4
            if dataPos + dataSize > len(self.data):
                raise ValueError("DDS mip data is truncated or isn't uncompressed 32-bit RGBA")
            newChunk = CTXRChunk()
            newChunk.size = dataSize
            newChunk.data = self.data[dataPos:dataPos+dataSize]
            ctxr.chunks.append(newChunk)
            dataPos += dataSize
            # afevis fix from vanilla ctxr cooker: keep the short side of rectangular mips from shrinking to zero.
            width, height = max(1, width // 2), max(1, height // 2)
        
        if dataPos != len(self.data):
            raise ValueError("DDS contains unexpected data beyond its declared mip levels")
        if source_header is None or source_digest != self.contentDigest():
            # calculate color bounds across every mip.
            channels = [self.data[i::4] for i in (2, 1, 0, 3)]
            ctxr.header.minRGBA = int.from_bytes(bytes(min(c) for c in channels), 'big')
            ctxr.header.maxRGBA = int.from_bytes(bytes(max(c) for c in channels), 'big')
            threshold = 128 if HAS_ALPHA_USE_128_THRESHOLD else 255
            ctxr.header.hasAlpha = int(min(channels[3]) < threshold)
        if HAS_ALPHA_USE_128_THRESHOLD:
            ctxr.header.hasAlpha = int(min(self.data[3::4]) < 128)
        return ctxr
    
    def writeToFile(self, file: BufferedWriter):
        self.header.writeToFile(file)
        file.write(self.data)

class DDSHeader:
    magic: bytes  # "DDS "
    hdrSize: int # 0x7C
    flags: int
    height: int
    width: int
    pitch: int
    depth: int
    numMipmaps: int
    reserved: bytes  # 44 bytes
    pixelFormat: DDSPixelFormat
    caps: list[int]  # 4 int32s
    reserved2: int
    
    def __init__(self):
        self.magic = b"DDS "
        self.hdrSize = 0x7C
        self.flags = 0
        self.height = 0
        self.width = 0
        self.pitch = 0
        self.depth = 0
        self.numMipmaps = 1
        self.reserved = b"\0" * 44
        self.pixelFormat = DDSPixelFormat()
        self.caps = [0, 0, 0, 0]
        self.reserved2 = 0
    
    def fromFile(self, file: BufferedReader):
        self.magic, self.hdrSize, self.flags, self.height, \
        self.width, self.pitch, self.depth, self.numMipmaps \
        = struct.unpack("<4s7I", file.read(0x20))
        self.reserved = file.read(44)
        self.pixelFormat.fromFile(file)
        self.caps = list(struct.unpack("<4I", file.read(0x10)))
        self.reserved2 = struct.unpack("<I", file.read(4))[0]
        return self
    
    def writeToFile(self, file: BufferedWriter):
        file.write(self.magic)
        file.write(struct.pack("<7I", self.hdrSize, self.flags, self.height, \
        self.width, self.pitch, self.depth, self.numMipmaps))
        file.write(self.reserved)
        self.pixelFormat.writeToFile(file)
        file.write(struct.pack("<5I", self.caps[0], self.caps[1], self.caps[2], self.caps[3], \
        self.reserved2))

class DDSPixelFormat:
    pxlFmtSize: int # 0x20
    flags: int
    fourcc: int
    bitCount: int
    bitMasks: list[int] # RGBA
    
    def __init__(self):
        self.pxlFmtSize = 0x20
        self.flags = 0
        self.fourcc = 0
        self.bitCount = 32
        self.bitMasks = [0xff0000, 0xff00, 0xff, 0xff000000]
    
    def fromFile(self, file: BufferedReader):
        self.pxlFmtSize, self.flags, self.fourcc, self.bitCount \
        = struct.unpack("<4I", file.read(0x10))
        self.bitMasks = list(struct.unpack("<4I", file.read(0x10)))
        
        return self
    
    def writeToFile(self, file: BufferedWriter):
        file.write(struct.pack("<8I", self.pxlFmtSize, self.flags, self.fourcc, self.bitCount, \
        self.bitMasks[0], self.bitMasks[1], self.bitMasks[2], self.bitMasks[3]))


ctxr_lookup_path = path.join(path.dirname(__file__), "ctxrmapping.txt")
