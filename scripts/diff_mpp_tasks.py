"""Diff the task records of two MPP14 files, field by field.

    python scripts/diff_mpp_tasks.py generated.mpp resaved-by-project.mpp

Tasks are matched by unique ID. Each file is decoded through its own
TASK_FIELD_MAP, so the two files may lay their fixed records out differently.
Reports: fixed-field value changes, meta bitmap changes (named by the field
whose entry the bit belongs to), and var-data changes. This is the tool for
locating which fields/bits Microsoft Project rewrites when you type a value
into a generated file and Save As.
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
                                PROPS_TASK_FIELD_MAP)

NF = json.load(open(os.path.join(os.path.dirname(__file__), "..", "pymppwriter", "native_fields.json")))["task"]
NF = {int(k): v for k, v in NF.items()}


def fname(tid):
    return NF.get(tid, f"field{tid}")


def decode_fixed(it, rec, rec2):
    src = rec if it.block == 0 else rec2
    o = it.offset
    if it.category == 0x13 and o + 4 <= len(src):
        return decode_timestamp(src, o)
    if it.category == 0x02 and o + 2 <= len(src):
        return struct.unpack_from("<H", src, o)[0]
    if it.category == 0x03 and o + 4 <= len(src):
        return struct.unpack_from("<i", src, o)[0]
    if it.category in (0x05, 0x65) and o + 8 <= len(src):
        return struct.unpack_from("<d", src, o)[0]
    if it.category == 0x48 and o + 16 <= len(src):
        return src[o:o + 16].hex()
    return src[o:o + 4].hex() if o + 4 <= len(src) else None


def load(path):
    ole = olefile.OleFileIO(path)
    r = lambda p: ole.openstream(p).read()
    _, props, _ = parse_props(r("   114/Props"))
    fm = parse_field_map(props[PROPS_TASK_FIELD_MAP])
    _, _, mitems = parse_fixed_meta(r("   114/TBkndTask/FixedMeta"), 47)
    recs = split_fixed_data(r("   114/TBkndTask/FixedData"), mitems)
    _, _, m2items = parse_fixed_meta(r("   114/TBkndTask/Fixed2Meta"), 92)
    recs2 = split_fixed_data(r("   114/TBkndTask/Fixed2Data"), m2items)
    _, vtable, _ = parse_var_meta(r("   114/TBkndTask/VarMeta"))
    vdata = r("   114/TBkndTask/Var2Data")
    tasks = {}
    for i, rec in enumerate(recs):
        if len(rec) < 100:
            continue
        uid = struct.unpack_from("<I", rec, 0)[0]
        rec2 = recs2[i] if i < len(recs2) else b""
        fields, bits = {}, {}
        for j, it in enumerate(fm):
            tid = it.type_value & 0xFFFF
            if it.in_fixed and tid not in fields:
                fields[tid] = decode_fixed(it, rec, rec2)
            b = meta_bit(mitems[i], m2items[i] if i < len(m2items) else b"", j)
            if b is not None and (tid not in bits):
                bits[tid] = b
        var = {typ: read_var(vdata, off) for typ, off in vtable.get(uid, {}).items()}
        tasks[uid] = dict(fields=fields, bits=bits, var=var,
                          meta=mitems[i], meta2=m2items[i] if i < len(m2items) else b"")
    return tasks


def main():
    a, b = sys.argv[1], sys.argv[2]
    ta, tb = load(a), load(b)
    only_a, only_b = sorted(ta.keys() - tb.keys()), sorted(tb.keys() - ta.keys())
    if only_a:
        print(f"only in {a}: uids {only_a}")
    if only_b:
        print(f"only in {b}: uids {only_b}")
    for uid in sorted(ta.keys() & tb.keys()):
        A, Bt = ta[uid], tb[uid]
        name = decode_unicode(A["var"].get(14, b"")) or decode_unicode(Bt["var"].get(14, b""))
        lines = []
        for tid in sorted(A["fields"].keys() | Bt["fields"].keys()):
            va, vb = A["fields"].get(tid), Bt["fields"].get(tid)
            if va != vb:
                lines.append(f"  fixed {fname(tid):28s} {va!r} -> {vb!r}")
        for tid in sorted(A["bits"].keys() | Bt["bits"].keys()):
            va, vb = A["bits"].get(tid), Bt["bits"].get(tid)
            if va != vb:
                lines.append(f"  bit   {fname(tid):28s} {va} -> {vb}")
        for typ in sorted(A["var"].keys() | Bt["var"].keys()):
            va, vb = A["var"].get(typ), Bt["var"].get(typ)
            if va != vb:
                sa = va.hex() if va is not None else "absent"
                sb = vb.hex() if vb is not None else "absent"
                lines.append(f"  var   {fname(typ):28s} {sa} -> {sb}")
        if lines:
            print(f"\nuid {uid} ({name!r}):")
            print("\n".join(lines))
    print("\n(done — fields not listed are identical)")


if __name__ == "__main__":
    main()
