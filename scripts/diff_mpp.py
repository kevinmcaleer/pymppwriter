"""Diff two MPP14 files across every entity class and the Props stream.

    python scripts/diff_mpp.py before.mpp after.mpp

Compares, matching records by unique id and decoding each file through its own
field maps: tasks, resources, assignments, relations, calendars, and the Props
entries. Prints only differences. Complements diff_mpp_tasks.py (task detail);
this is the tool for "what did Project change when it saved?".
"""
import sys
import struct
import json
import os
import olefile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pymppwriter.blocks import (parse_props, parse_field_map, parse_fixed_meta,
                                split_fixed_data, parse_var_meta, read_var,
                                decode_timestamp, decode_unicode, meta_bit,
                                PROPS_TASK_FIELD_MAP, PROPS_RESOURCE_FIELD_MAP,
                                PROPS_ASSIGNMENT_FIELD_MAP)

NFALL = json.load(open(os.path.join(os.path.dirname(__file__), "..", "pymppwriter", "native_fields.json")))
CLASSES = {
    "Task": ("TBkndTask", PROPS_TASK_FIELD_MAP, 47, NFALL.get("task", {})),
    "Rsc": ("TBkndRsc", PROPS_RESOURCE_FIELD_MAP, 37, NFALL.get("resource", {})),
    "Assn": ("TBkndAssn", PROPS_ASSIGNMENT_FIELD_MAP, 34, NFALL.get("assignment", {})),
}


def decode_fixed(it, rec, rec2):
    src = rec if it.block == 0 else rec2
    o = it.offset
    try:
        if it.category == 0x13:
            return decode_timestamp(src, o)
        if it.category == 0x02:
            return struct.unpack_from("<H", src, o)[0]
        if it.category == 0x03:
            return struct.unpack_from("<i", src, o)[0]
        if it.category in (0x05, 0x65, 0x66):
            return struct.unpack_from("<d", src, o)[0]
        if it.category == 0x48:
            return src[o:o + 16].hex()
        return src[o:o + 4].hex()
    except struct.error:
        return None


def load_class(ole, storage, map_key, meta_size):
    r = lambda p: ole.openstream(p).read()
    _, props, _ = parse_props(r("   114/Props"))
    fm = parse_field_map(props[map_key])
    _, _, mitems = parse_fixed_meta(r(f"   114/{storage}/FixedMeta"), meta_size)
    recs = split_fixed_data(r(f"   114/{storage}/FixedData"), mitems)
    m2d = r(f"   114/{storage}/Fixed2Meta")
    m2n = struct.unpack_from("<I", m2d, 8)[0]
    m2items = parse_fixed_meta(m2d, (len(m2d) - 16) // m2n)[2] if m2n else []
    recs2 = split_fixed_data(r(f"   114/{storage}/Fixed2Data"), m2items)
    _, vtable, _ = parse_var_meta(r(f"   114/{storage}/VarMeta"))
    vdata = r(f"   114/{storage}/Var2Data")
    out = {}
    for i, rec in enumerate(recs):
        if len(rec) <= 16:
            continue
        uid = struct.unpack_from("<I", rec, 0)[0]
        rec2 = recs2[i] if i < len(recs2) else b""
        fields, bits = {}, {}
        for j, it in enumerate(fm):
            tid = it.type_value & 0xFFFF
            if it.in_fixed and tid not in fields:
                fields[tid] = decode_fixed(it, rec, rec2)
            b = meta_bit(mitems[i], m2items[i] if i < len(m2items) else b"", j)
            if b is not None and tid not in bits:
                bits[tid] = b
        var = {typ: read_var(vdata, off) for typ, off in vtable.get(uid, {}).items()}
        out[uid] = dict(fields=fields, bits=bits, var=var)
    return out


def diff_class(label, nf, ta, tb):
    nf = {int(k): v for k, v in nf.items()}
    fname = lambda tid: nf.get(tid, f"field{tid}")
    only_a, only_b = sorted(ta.keys() - tb.keys()), sorted(tb.keys() - ta.keys())
    if only_a:
        print(f"[{label}] only in A: uids {only_a}")
    if only_b:
        print(f"[{label}] only in B: uids {only_b}")
    for uid in sorted(ta.keys() & tb.keys()):
        A, Bv = ta[uid], tb[uid]
        lines = []
        for tid in sorted(A["fields"].keys() | Bv["fields"].keys()):
            va, vb = A["fields"].get(tid), Bv["fields"].get(tid)
            if va != vb:
                lines.append(f"  fixed {fname(tid):28s} {va!r} -> {vb!r}")
        for tid in sorted(A["bits"].keys() | Bv["bits"].keys()):
            va, vb = A["bits"].get(tid), Bv["bits"].get(tid)
            if va != vb:
                lines.append(f"  bit   {fname(tid):28s} {va} -> {vb}")
        for typ in sorted(A["var"].keys() | Bv["var"].keys()):
            va, vb = A["var"].get(typ), Bv["var"].get(typ)
            if va != vb:
                sa = va.hex() if va is not None else "absent"
                sb = vb.hex() if vb is not None else "absent"
                lines.append(f"  var   {fname(typ):28s} ({len(va) if va else 0}b) {sa[:80]} -> ({len(vb) if vb else 0}b) {sb[:80]}")
        if lines:
            print(f"\n[{label}] uid {uid}:")
            print("\n".join(lines))


def main():
    a, b = sys.argv[1], sys.argv[2]
    oa, ob = olefile.OleFileIO(a), olefile.OleFileIO(b)
    # Props
    _, pa, _ = parse_props(oa.openstream("   114/Props").read())
    _, pb, _ = parse_props(ob.openstream("   114/Props").read())
    print("=== Props")
    for key in sorted(pa.keys() | pb.keys()):
        va, vb = pa.get(key), pb.get(key)
        if va != vb:
            fa = va.hex()[:60] if va is not None else "absent"
            fb = vb.hex()[:60] if vb is not None else "absent"
            if va is not None and len(va) == 4 and vb is not None and len(vb) == 4:
                fa, fb = str(struct.unpack("<I", va)[0]), str(struct.unpack("<I", vb)[0])
            print(f"  key {key} (0x{key:X}): {fa} -> {fb}")
    # entity classes
    for label, (storage, map_key, msize, nf) in CLASSES.items():
        print(f"\n=== {label}")
        diff_class(label, nf, load_class(oa, storage, map_key, msize),
                   load_class(ob, storage, map_key, msize))
    # calendars (no field map: raw records keyed by position)
    print("\n=== Cal (raw)")
    for name, ole in (("A", oa), ("B", ob)):
        d = ole.openstream("   114/TBkndCal/FixedMeta").read()
        cnt = struct.unpack_from("<I", d, 8)[0]
        mitems = [d[16 + i * 10:16 + (i + 1) * 10] for i in range(cnt)]
        recs = split_fixed_data(ole.openstream("   114/TBkndCal/FixedData").read(), mitems)
        rows = [struct.unpack("<3i", r) for r in recs if len(r) == 12]
        print(f"  {name}: {cnt} records, 12-byte rows: {rows}")


if __name__ == "__main__":
    main()
