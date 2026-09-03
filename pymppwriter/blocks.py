"""Decoders/encoders for the block structures inside an MPP14 file.

Layouts derived from the public behaviour of the LGPL MPXJ reader and from
diffing files saved by Microsoft Project.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

MAGIC = 0xFADFADBA
EPOCH = datetime(1983, 12, 31)

PROPS_TASK_FIELD_MAP = 131092
PROPS_RESOURCE_FIELD_MAP = 131093
PROPS_RELATION_FIELD_MAP = 131094
PROPS_ASSIGNMENT_FIELD_MAP = 131095
PROPS_PROJECT_START_DATE = 37748738
PROPS_TITLE = 37748744


# ---------------------------------------------------------------- Props ----
PROPS_TYPES: Dict[int, int] = {}   # key -> type code (third dword of each entry; 0/2/4/9 observed)


def parse_props(data: bytes) -> Tuple[bytes, Dict[int, bytes], List[int]]:
    """Return (16-byte header, {key: value}, key order). Entry type codes are kept in PROPS_TYPES."""
    header = data[:16]
    count = struct.unpack_from("<H", header, 12)[0]
    pos, out, order = 16, {}, []
    for _ in range(count):
        if len(data) - pos < 12:
            break
        size, key, ptype = struct.unpack_from("<III", data, pos)
        pos += 12
        val = data[pos:pos + size]
        pos += size + (size & 1)  # 2-byte alignment
        out[key] = val
        PROPS_TYPES[key] = ptype
        order.append(key)
    return header, out, order


def build_props(header: bytes, values: Dict[int, bytes], order: List[int]) -> bytes:
    hdr = bytearray(header)
    struct.pack_into("<H", hdr, 12, len(order))
    body = bytearray()
    for key in order:
        val = values[key]
        body += struct.pack("<III", len(val), key, PROPS_TYPES.get(key, 0)) + val
        if len(val) & 1:
            body += b"\0"
    total = len(hdr) + len(body)
    struct.pack_into("<II", hdr, 0, total - 4, total - 4)   # header dwords 0,1 = stream size - 4
    return bytes(hdr) + bytes(body)


# ------------------------------------------------------------- FieldMap ----
@dataclass
class FieldItem:
    type_value: int        # MS Project field ID (TaskField/ResourceField numeric)
    block: int             # 0 = FixedData, 1 = Fixed2Data
    offset: int            # byte offset within the fixed record (65535 = not fixed)
    var_key: int           # key in Var2Data when stored as variable data
    category: int
    mask: int
    raw: bytes

    @property
    def in_fixed(self) -> bool:
        return self.category not in (0x0B, 0x64) and self.offset != 65535

    @property
    def in_meta(self) -> bool:
        return self.category in (0x0B, 0x64)


def parse_field_map(data: bytes) -> List[FieldItem]:
    items, last, block = [], 0, 0
    for i in range(0, len(data) - 27, 28):
        mask = struct.unpack_from("<I", data, i)[0]
        offset = struct.unpack_from("<H", data, i + 4)[0]
        var_key = data[i + 6]
        type_value = struct.unpack_from("<I", data, i + 12)[0]
        category = struct.unpack_from("<H", data, i + 20)[0]
        if category not in (0x0B, 0x64) and offset != 65535:
            if offset < last:
                block += 1
            last = offset
        items.append(FieldItem(type_value, block, offset, var_key, category, mask, data[i:i + 28]))
    return items


# -------------------------------------------------------- Fixed blocks -----
def parse_fixed_meta(data: bytes, item_size: int) -> Tuple[bytes, int, List[bytes]]:
    magic, unk, count, unk2 = struct.unpack_from("<IIII", data, 0)
    assert magic == MAGIC, hex(magic)
    n = (len(data) - 16) // item_size
    items = [data[16 + i * item_size:16 + (i + 1) * item_size] for i in range(n)]
    return data[:16], count, items


def build_fixed_meta(header: bytes, items: List[bytes]) -> bytes:
    hdr = bytearray(header)
    struct.pack_into("<I", hdr, 8, len(items))
    return bytes(hdr) + b"".join(items)


def split_fixed_data(data: bytes, meta_items: List[bytes]) -> List[bytes]:
    out = []
    for i, m in enumerate(meta_items):
        off = struct.unpack_from("<I", m, 4)[0]
        if i + 1 < len(meta_items):
            nxt = struct.unpack_from("<I", meta_items[i + 1], 4)[0]
        else:
            nxt = len(data)
        out.append(data[off:nxt])
    return out


# ---------------------------------------------------------- Var blocks -----
def parse_var_meta(data: bytes) -> Tuple[bytes, Dict[int, Dict[int, int]], List[Tuple[int, int, int, int]]]:
    """VarMeta12: 24-byte header then 12-byte entries (uid, offset, type, unk)."""
    magic, unk, count, unk2, unk3, data_size = struct.unpack_from("<IIIIII", data, 0)
    table: Dict[int, Dict[int, int]] = {}
    entries = []
    pos = 24
    for _ in range(count):
        if len(data) - pos < 12:
            break
        uid, off, typ, unk4 = struct.unpack_from("<IIHH", data, pos)
        pos += 12
        table.setdefault(uid, {})[typ] = off
        entries.append((uid, off, typ, unk4))
    return data[:24], table, entries


def read_var(data: bytes, off: int) -> bytes:
    size = struct.unpack_from("<I", data, off)[0]
    return data[off + 4:off + 4 + size]


TASK_FIELD_HI, RESOURCE_FIELD_HI, ASSIGNMENT_FIELD_HI = 0x0B40, 0x0C40, 0x0F40


def build_var_blocks(header: bytes, values: List[Tuple[int, int, bytes]], field_hi: int = TASK_FIELD_HI) -> Tuple[bytes, bytes]:
    """values: [(uid, type, payload)] -> (VarMeta, Var2Data).

    Each entry is (uid, offset, fieldId low16, fieldId high16); the high word is the
    native field-class prefix (0x0B40 tasks, 0x0C40 resources, 0x0F40 assignments).
    Entries must be ordered by (uid, fieldId)."""
    meta = bytearray(header)
    var = bytearray()
    entries = bytearray()
    for uid, typ, payload in sorted(values, key=lambda v: (v[0], v[1])):
        entries += struct.pack("<IIHH", uid, len(var), typ, field_hi)
        var += struct.pack("<I", len(payload)) + payload
    struct.pack_into("<I", meta, 8, len(values))
    struct.pack_into("<I", meta, 20, len(var))
    return bytes(meta) + bytes(entries), bytes(var)


# ------------------------------------------------------- primitives --------
def decode_timestamp(b: bytes, off: int) -> Optional[datetime]:
    time, days = struct.unpack_from("<HH", b, off)
    if days <= 1 or days == 65535:
        return None
    if time == 65535:
        time = 0
    return EPOCH + timedelta(days=days, seconds=time * 6)


def encode_timestamp(dt: Optional[datetime]) -> bytes:
    if dt is None:
        return b"\xff\xff\xff\xff"
    delta = dt - EPOCH
    days = delta.days
    tenths = delta.seconds // 6
    return struct.pack("<HH", tenths, days)


def encode_unicode(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\0\0"


def decode_unicode(b: bytes) -> str:
    return b.decode("utf-16-le", errors="replace").split("\0", 1)[0]


# duration units (MS Project codes) -> tenths-of-a-minute divisor
DURATION_UNITS = {3: ("m", 10), 5: ("h", 600), 7: ("d", 4800), 9: ("w", 24000), 11: ("mo", 96000)}
