"""Minimal writer for Microsoft Compound File Binary (OLE2) containers.

Implements enough of [MS-CFB] v3 (512-byte sectors, 64-byte mini sectors,
4096-byte mini-stream cutoff) to produce files that Apache POI / olefile /
MS Project can read. Written from the public [MS-CFB] specification.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF

SECTOR = 512
MINI_SECTOR = 64
MINI_CUTOFF = 4096
FAT_PER_SECTOR = SECTOR // 4

TYPE_STORAGE, TYPE_STREAM, TYPE_ROOT = 1, 2, 5


@dataclass
class Storage:
    children: Dict[str, Union["Storage", bytes]] = field(default_factory=dict)

    def add_stream(self, name: str, data: bytes) -> None:
        self.children[name] = bytes(data)

    def add_storage(self, name: str) -> "Storage":
        s = self.children.get(name)
        if not isinstance(s, Storage):
            s = Storage()
            self.children[name] = s
        return s

    def set_path(self, path: str, data: bytes) -> None:
        parts = path.split("/")
        s = self
        for p in parts[:-1]:
            s = s.add_storage(p)
        s.add_stream(parts[-1], data)

    def storage_path(self, path: str) -> "Storage":
        s = self
        for p in path.split("/"):
            s = s.add_storage(p)
        return s


class _Entry:
    __slots__ = ("name", "etype", "data", "child", "left", "right", "start", "size", "index", "clsid")

    def __init__(self, name: str, etype: int, data: Optional[bytes] = None):
        self.name = name
        self.etype = etype
        self.data = data
        self.child = NOSTREAM
        self.left = NOSTREAM
        self.right = NOSTREAM
        self.start = ENDOFCHAIN
        self.size = 0
        self.index = -1
        self.clsid = b"\0" * 16


def _name_key(name: str):
    # [MS-CFB] 2.6.4: compare by UTF-16 length first, then upper-cased code units.
    u = name.encode("utf-16-le")
    return (len(u), name.upper().encode("utf-16-le"))


def _build_tree(entries: List[_Entry]) -> int:
    """Return index of root of a balanced binary tree over sorted entries."""
    if not entries:
        return NOSTREAM
    mid = len(entries) // 2
    node = entries[mid]
    node.left = _build_tree(entries[:mid])
    node.right = _build_tree(entries[mid + 1:])
    return node.index


def _pad(data: bytes, unit: int) -> bytes:
    rem = len(data) % unit
    return data if rem == 0 else data + b"\0" * (unit - rem)


def write_cfb(root: Storage, root_clsid: bytes = b"\0" * 16) -> bytes:
    # ---- flatten directory -------------------------------------------------
    entries: List[_Entry] = []
    root_entry = _Entry("Root Entry", TYPE_ROOT)
    root_entry.clsid = root_clsid
    entries.append(root_entry)

    def flatten(storage: Storage, parent: _Entry) -> None:
        kids: List[_Entry] = []
        for name, val in storage.children.items():
            if isinstance(val, Storage):
                e = _Entry(name, TYPE_STORAGE)
            else:
                e = _Entry(name, TYPE_STREAM, val)
            entries.append(e)
            kids.append(e)
        for e in kids:
            e.index = entries.index(e)
        kids.sort(key=lambda e: _name_key(e.name))
        parent.child = _build_tree(kids)
        for e in kids:
            if e.etype == TYPE_STORAGE:
                flatten(storage.children[e.name], e)

    root_entry.index = 0
    flatten(root, root_entry)
    for i, e in enumerate(entries):
        e.index = i

    # ---- mini stream -------------------------------------------------------
    mini_stream = bytearray()
    minifat: List[int] = []
    for e in entries:
        if e.etype == TYPE_STREAM and 0 < len(e.data) < MINI_CUTOFF:
            e.size = len(e.data)
            e.start = len(minifat)
            padded = _pad(e.data, MINI_SECTOR)
            n = len(padded) // MINI_SECTOR
            mini_stream += padded
            minifat.extend(range(len(minifat) + 1, len(minifat) + n))
            minifat.append(ENDOFCHAIN)
    root_entry.size = len(mini_stream)

    # ---- regular sectors ---------------------------------------------------
    sectors: List[bytes] = []          # sector payloads (512 bytes each)
    fat: List[int] = []                # FAT entry per sector

    def add_chain(data: bytes) -> int:
        padded = _pad(data, SECTOR)
        n = len(padded) // SECTOR
        if n == 0:
            return ENDOFCHAIN
        first = len(sectors)
        for i in range(n):
            sectors.append(padded[i * SECTOR:(i + 1) * SECTOR])
            fat.append(first + i + 1 if i < n - 1 else ENDOFCHAIN)
        return first

    # large streams
    for e in entries:
        if e.etype == TYPE_STREAM and len(e.data) >= MINI_CUTOFF:
            e.size = len(e.data)
            e.start = add_chain(e.data)

    # mini stream itself lives in regular sectors, pointed to by root entry
    root_entry.start = add_chain(bytes(mini_stream)) if mini_stream else ENDOFCHAIN

    # minifat
    minifat_start, minifat_count = ENDOFCHAIN, 0
    if minifat:
        raw = b"".join(struct.pack("<I", v) for v in minifat)
        raw = _pad(raw, SECTOR)
        # pad unused minifat slots with FREESECT
        raw = raw[: len(minifat) * 4] + b"\xff" * (len(raw) - len(minifat) * 4)
        minifat_start = add_chain(raw)
        minifat_count = len(raw) // SECTOR

    # directory
    dir_raw = bytearray()
    for e in entries:
        dir_raw += _dir_entry(e)
    dir_raw = _pad(bytes(dir_raw), SECTOR)
    # fill trailing unused entries with valid empty entries
    n_slots = len(dir_raw) // 128
    for i in range(len(entries), n_slots):
        dir_raw = dir_raw[: i * 128] + _dir_entry(None) + dir_raw[(i + 1) * 128:]
    dir_start = add_chain(bytes(dir_raw))

    # FAT sectors (iterate: FAT sectors — and any DIFAT sectors listing them
    # beyond the header's 109 slots — need FAT entries too)
    DIFAT_PER_SECTOR = FAT_PER_SECTOR - 1      # last dword chains to the next DIFAT sector
    n_data = len(sectors)
    n_fat = n_difat = 0
    while True:
        needed_fat = -(-(n_data + n_fat + n_difat) // FAT_PER_SECTOR)
        needed_difat = -(-max(0, needed_fat - 109) // DIFAT_PER_SECTOR)
        if (needed_fat, needed_difat) == (n_fat, n_difat):
            break
        n_fat, n_difat = needed_fat, needed_difat

    fat_full = fat + [FATSECT] * n_fat + [DIFSECT] * n_difat
    fat_full += [FREESECT] * (n_fat * FAT_PER_SECTOR - len(fat_full))
    fat_raw = b"".join(struct.pack("<I", v) for v in fat_full)
    fat_sector_ids = list(range(n_data, n_data + n_fat))
    difat_sector_ids = list(range(n_data + n_fat, n_data + n_fat + n_difat))
    for i in range(n_fat):
        sectors.append(fat_raw[i * SECTOR:(i + 1) * SECTOR])
    for i in range(n_difat):
        chunk = fat_sector_ids[109 + i * DIFAT_PER_SECTOR: 109 + (i + 1) * DIFAT_PER_SECTOR]
        chunk += [FREESECT] * (DIFAT_PER_SECTOR - len(chunk))
        nxt = difat_sector_ids[i + 1] if i + 1 < n_difat else ENDOFCHAIN
        sectors.append(b"".join(struct.pack("<I", v) for v in chunk) + struct.pack("<I", nxt))

    # ---- header ------------------------------------------------------------
    hdr = bytearray(SECTOR)
    struct.pack_into("<8s16sHHHHH6sIIIIIIIII", hdr, 0,
                     b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"\0" * 16,
                     0x003E, 0x0003, 0xFFFE, 9, 6, b"\0" * 6,
                     0,               # number of directory sectors (v3: 0)
                     n_fat,
                     dir_start,
                     0,               # transaction signature
                     MINI_CUTOFF,
                     minifat_start, minifat_count,
                     difat_sector_ids[0] if n_difat else ENDOFCHAIN, n_difat)
    difat = fat_sector_ids[:109] + [FREESECT] * max(0, 109 - len(fat_sector_ids))
    struct.pack_into("<109I", hdr, 76, *difat)

    return bytes(hdr) + b"".join(sectors)


def _dir_entry(e: Optional[_Entry]) -> bytes:
    if e is None:
        return struct.pack("<64sHBBIII16sIQQIQ", b"\0" * 64, 0, 0, 0,
                           NOSTREAM, NOSTREAM, NOSTREAM, b"\0" * 16, 0, 0, 0, 0, 0)
    name = e.name.encode("utf-16-le") + b"\0\0"
    if len(name) > 64:
        raise ValueError(f"name too long: {e.name!r}")
    return struct.pack("<64sHBBIII16sIQQIQ",
                       name.ljust(64, b"\0"), len(name), e.etype, 1,  # colour: black
                       e.left, e.right, e.child, e.clsid, 0, 0, 0,
                       e.start if e.start != ENDOFCHAIN else (0 if e.etype != TYPE_STREAM else ENDOFCHAIN),
                       e.size)


def load_cfb(path: str) -> Storage:
    """Read an existing compound file into a Storage tree (via olefile)."""
    import olefile
    ole = olefile.OleFileIO(path)
    root = Storage()
    for parts in ole.listdir(streams=True, storages=True):
        p = "/".join(parts)
        if ole.get_type(p) == olefile.STGTY_STORAGE:
            root.storage_path(p)
        else:
            root.set_path(p, ole.openstream(p).read())
    return root
