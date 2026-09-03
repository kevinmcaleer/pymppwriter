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
PROPS_DEFAULT_CALENDAR_NAME = 37748750    # UTF-16 name + 4 NUL bytes
# record-count dwords: Project sizes its tables from these on load and drops
# records beyond the count (verified against four Project-written files)
PROPS_TASK_RECORD_COUNT = 16777217        # 0x1000001, includes stubs + uid-0 summary
PROPS_RESOURCE_RECORD_COUNT = 16777218    # 0x1000002
PROPS_ASSN_RECORD_COUNT = 16777220        # 0x1000004
PROPS_REL_RECORD_COUNT = 16777221         # 0x1000005


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


def build_fixed_meta(header: bytes, items: List[bytes], data_len: Optional[int] = None) -> bytes:
    hdr = bytearray(header)
    struct.pack_into("<I", hdr, 8, len(items))
    if data_len is not None:
        struct.pack_into("<I", hdr, 12, data_len)   # header dword 3 = FixedData byte length
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


# ------------------------------------------------------- meta bitmaps ------
# A FixedMeta / Fixed2Meta item is: uint32 flags, uint32 offset-in-FixedData,
# then a bitmap with one bit per TASK_FIELD_MAP entry (little-endian bit order).
# FixedMeta carries entries 0..(item_size-8)*8-1; Fixed2Meta continues from there.
# Boolean fields (category 0x0B/0x64) store their value in their entry's bit;
# for other fields the bit marks the field as populated.

def meta_bit(meta: bytes, meta2: bytes, entry_index: int) -> Optional[int]:
    nbits0 = (len(meta) - 8) * 8
    buf, i = (meta, entry_index) if entry_index < nbits0 else (meta2, entry_index - nbits0)
    byte = 8 + i // 8
    if byte >= len(buf):
        return None
    return (buf[byte] >> (i % 8)) & 1


def set_meta_bit(meta: bytearray, meta2: bytearray, entry_index: int, value: bool) -> None:
    nbits0 = (len(meta) - 8) * 8
    buf, i = (meta, entry_index) if entry_index < nbits0 else (meta2, entry_index - nbits0)
    byte = 8 + i // 8
    if byte >= len(buf):
        return
    if value:
        buf[byte] |= 1 << (i % 8)
    else:
        buf[byte] &= ~(1 << (i % 8)) & 0xFF


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


# ------------------------------------------------------ calendar data ------
CAL_DAY_NONWORKING, CAL_DAY_DEFAULT, CAL_DAY_WORKING = 0, 1, 2
# recurrence dwords for a plain date-range exception, byte-copied from a
# Project-written file (zeroes make the reader reject the exception)
_EXC_RECUR_1, _EXC_RECUR_2 = 0x238CF1F7, 0xC4DB7B9F


def build_calendar_data(days, exceptions=()) -> bytes:
    """Calendar definition blob (var-data key 8 on a TBkndCal record).

    days: 7 (day_type, ranges) tuples, SUNDAY first; ranges are
    (start_minute, end_minute) pairs from midnight, at most 5 per day and only
    meaningful for CAL_DAY_WORKING.
    exceptions: (from_date, to_date, name) tuples of non-working days, sorted.

    Each 60-byte day block: uint16 day type, uint16 range count, range start
    times as uint16 tenths-of-a-minute at +8 (stride 2), range durations as
    uint32 tenths at +20 (stride 4). Exceptions: uint32 count, then per
    exception a 92-byte record (uint16 from-day, uint16 to-day, uint16 day
    count, recurrence data at +72, uint32 name byte length at +88) followed by
    the UTF-16 name and 2 bytes of padding.
    """
    if len(days) != 7:
        raise ValueError("days must have exactly 7 entries, Sunday first")
    out = bytearray()
    for dtype, ranges in days:
        b = bytearray(60)
        struct.pack_into("<H", b, 0, dtype)
        if dtype == CAL_DAY_WORKING:
            ranges = list(ranges)[:5]
            struct.pack_into("<H", b, 2, len(ranges))
            for i, (start, end) in enumerate(ranges):
                struct.pack_into("<H", b, 8 + 2 * i, start * 10)
                struct.pack_into("<I", b, 20 + 4 * i, (end - start) * 10)
        out += b
    if exceptions:
        out += struct.pack("<I", len(exceptions))
        for from_date, to_date, name in exceptions:
            rec = bytearray(92)
            d1 = (from_date - EPOCH.date()).days
            d2 = (to_date - EPOCH.date()).days
            struct.pack_into("<HH", rec, 0, d1, d2)
            struct.pack_into("<H", rec, 4, d2 - d1 + 1)
            struct.pack_into("<I", rec, 72, 1)
            struct.pack_into("<I", rec, 76, _EXC_RECUR_1)
            struct.pack_into("<I", rec, 80, 1)
            struct.pack_into("<I", rec, 84, _EXC_RECUR_2)
            nb = (name + "\0").encode("utf-16-le")
            struct.pack_into("<I", rec, 88, len(nb))
            out += rec + nb + b"\0" * ((-len(nb)) % 4)   # next record 4-byte aligned
    return bytes(out)


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
