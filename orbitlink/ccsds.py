"""CCSDS-style space packets and sync-marker transfer frames.

This matches the format used by the companion flight software:

Space packet (CCSDS 133.0-B primary header + CRC-16 packet error control)::

    [0..1] version(3)=0 | type(1) | sec_hdr(1) | APID(11)
    [2..3] seq_flags(2)=0b11 | seq_count(14)
    [4..5] data length = len(user data + CRC) - 1
    [6.. ] user data, CRC-16/CCITT-FALSE over header + user data

Transfer frame::

    ASM 0x1ACFFC1D | SCID(2) | VCID(1) | count(1) | length(2) | data | FECF CRC-16(2)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

ASM = b"\x1a\xcf\xfc\x1d"
PRIMARY_HEADER_LEN = 6
CRC_LEN = 2
MAX_PACKET_LEN = 256
FRAME_HEADER_LEN = 10
MAX_FRAME_DATA = 512
SEQ_MASK = 0x3FFF
MAX_APID = 0x7FF


def _make_table() -> list[int]:
    table = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1)
        table.append(c & 0xFFFF)
    return table


_TABLE = _make_table()


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


class PacketType(IntEnum):
    TM = 0
    TC = 1


class DecodeError(ValueError):
    pass


@dataclass
class SpacePacket:
    apid: int
    seq: int
    data: bytes
    ptype: PacketType = PacketType.TM
    sec_hdr: bool = False

    def encode(self) -> bytes:
        if not 0 <= self.apid <= MAX_APID:
            raise ValueError(f"APID out of range: {self.apid}")
        total = PRIMARY_HEADER_LEN + len(self.data) + CRC_LEN
        if total > MAX_PACKET_LEN:
            raise ValueError("packet too long")
        w0 = (int(self.ptype) << 12) | (0x0800 if self.sec_hdr else 0) | self.apid
        w1 = 0xC000 | (self.seq & SEQ_MASK)
        hdr = struct.pack(">HHH", w0, w1, len(self.data) + CRC_LEN - 1)
        body = hdr + bytes(self.data)
        return body + struct.pack(">H", crc16(body))

    @classmethod
    def decode(cls, buf: bytes) -> tuple["SpacePacket", int]:
        """Decode one packet at the start of ``buf``; returns (packet, bytes consumed)."""
        if len(buf) < PRIMARY_HEADER_LEN + CRC_LEN:
            raise DecodeError("too short")
        w0, w1, dlen = struct.unpack_from(">HHH", buf)
        if w0 >> 13:
            raise DecodeError("bad version")
        total = PRIMARY_HEADER_LEN + dlen + 1
        if total > MAX_PACKET_LEN:
            raise DecodeError("too long")
        if dlen + 1 < CRC_LEN or total > len(buf):
            raise DecodeError("length mismatch")
        (want,) = struct.unpack_from(">H", buf, total - CRC_LEN)
        if crc16(buf[: total - CRC_LEN]) != want:
            raise DecodeError("bad crc")
        pkt = cls(
            apid=w0 & MAX_APID,
            seq=w1 & SEQ_MASK,
            data=bytes(buf[PRIMARY_HEADER_LEN : total - CRC_LEN]),
            ptype=PacketType((w0 >> 12) & 1),
            sec_hdr=bool(w0 & 0x0800),
        )
        return pkt, total


def split_packets(data: bytes) -> list[SpacePacket]:
    """Decode consecutive packets from a frame data field (stops at the first error)."""
    out: list[SpacePacket] = []
    off = 0
    while off < len(data):
        try:
            pkt, n = SpacePacket.decode(data[off:])
        except DecodeError:
            break
        out.append(pkt)
        off += n
    return out


@dataclass
class Frame:
    scid: int
    vcid: int
    count: int
    data: bytes

    def encode(self) -> bytes:
        if len(self.data) > MAX_FRAME_DATA:
            raise ValueError("frame data too long")
        body = struct.pack(">HBBH", self.scid, self.vcid, self.count & 0xFF, len(self.data)) + self.data
        return ASM + body + struct.pack(">H", crc16(body))


@dataclass
class DeframerStats:
    frames_ok: int = 0
    crc_errors: int = 0
    length_errors: int = 0


@dataclass
class Deframer:
    """Byte-stream deframer: ASM hunt, header, body, FECF check."""

    stats: DeframerStats = field(default_factory=DeframerStats)
    _buf: bytearray = field(default_factory=bytearray)

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buf.extend(chunk)
        frames: list[Frame] = []
        while True:
            idx = self._buf.find(ASM)
            if idx < 0:
                # keep a possible partial ASM at the tail
                del self._buf[: max(0, len(self._buf) - 3)]
                return frames
            if idx:
                del self._buf[:idx]
            if len(self._buf) < FRAME_HEADER_LEN:
                return frames
            scid, vcid, count, length = struct.unpack_from(">HBBH", self._buf, 4)
            if length > MAX_FRAME_DATA:
                self.stats.length_errors += 1
                del self._buf[:4]
                continue
            total = FRAME_HEADER_LEN + length + CRC_LEN
            if len(self._buf) < total:
                return frames
            body = bytes(self._buf[4 : FRAME_HEADER_LEN + length])
            (want,) = struct.unpack_from(">H", self._buf, FRAME_HEADER_LEN + length)
            if crc16(body) == want:
                frames.append(Frame(scid, vcid, count, bytes(self._buf[FRAME_HEADER_LEN : FRAME_HEADER_LEN + length])))
                self.stats.frames_ok += 1
                del self._buf[:total]
            else:
                self.stats.crc_errors += 1
                del self._buf[:4]  # resync after the bad ASM
