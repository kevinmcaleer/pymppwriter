import sys, struct, json, olefile
from pymppwriter.blocks import *
NF = json.load(open("pymppwriter/native_fields.json"))["task"]; NF={int(k):v for k,v in NF.items()}
DATES={"START","FINISH","EARLY_START","EARLY_FINISH","LATE_START","LATE_FINISH","CREATED","CONSTRAINT_DATE","ACTUAL_START","ACTUAL_FINISH","SCHEDULED_START","SCHEDULED_FINISH","DEADLINE","STOP","RESUME","RESUME_NO_EARLIER_THAN","BASELINE_START","BASELINE_FINISH"}
path = sys.argv[1]; ole = olefile.OleFileIO(path); r = lambda p: ole.openstream(p).read()
hdr, props, order = parse_props(r("   114/Props"))
fm = parse_field_map(props[PROPS_TASK_FIELD_MAP])
print("--- fixed layout (block, offset, cat, native id, name)")
for it in sorted([i for i in fm if i.in_fixed], key=lambda i:(i.block,i.offset)):
    print(f"  blk{it.block} {it.offset:4d} 0x{it.category:02X} {it.type_value&0xFFFF:5d} {NF.get(it.type_value&0xFFFF,'?')}")
print("--- meta-bit flags (cat 0x0B/0x64): id name mask")
for it in fm:
    if it.in_meta: print(f"  blk{0 if it.category==0x0B else 1} {it.type_value&0xFFFF:5d} {NF.get(it.type_value&0xFFFF,'?'):28s} mask=0x{it.mask:08X}")
mh, mcount, mitems = parse_fixed_meta(r("   114/TBkndTask/FixedMeta"), 47)
recs = split_fixed_data(r("   114/TBkndTask/FixedData"), mitems)
m2h, m2count, m2items = parse_fixed_meta(r("   114/TBkndTask/Fixed2Meta"), 92)
recs2 = split_fixed_data(r("   114/TBkndTask/Fixed2Data"), m2items)
vh, vtable, ventries = parse_var_meta(r("   114/TBkndTask/VarMeta")); vdata = r("   114/TBkndTask/Var2Data")
print(f"\nFixedMeta hdr={mh.hex()} n={mcount}  Fixed2Meta hdr={m2h.hex()} n={m2count}  VarMeta hdr={vh.hex()} n={len(ventries)}")
for i, rec in enumerate(recs):
    print(f"\nTASK rec{i} len={len(rec)} len2={len(recs2[i]) if i<len(recs2) else None}")
    print("  meta :", mitems[i].hex()); print("  meta2:", m2items[i].hex() if i<len(m2items) else None)
    if len(rec) < 8: print("  raw:", rec.hex()); continue
    uid = struct.unpack_from("<I", rec, 0)[0]
    out=[]
    for it in sorted([i for i in fm if i.in_fixed], key=lambda i:(i.block,i.offset)):
        src = rec if it.block==0 else recs2[i]
        n = NF.get(it.type_value&0xFFFF,'?')
        if it.offset+2 > len(src): continue
        if it.category==0x13: v=decode_timestamp(src,it.offset) if it.offset+4<=len(src) else None
        elif it.category==0x02: v=struct.unpack_from("<H",src,it.offset)[0]
        elif it.category==0x03: v=struct.unpack_from("<i",src,it.offset)[0]
        elif it.category in (0x65,0x05) and it.offset+8<=len(src): v=struct.unpack_from("<d",src,it.offset)[0]
        elif it.category==0x48 and it.offset+16<=len(src): v=src[it.offset:it.offset+16].hex()
        else: v=src[it.offset:it.offset+4].hex()
        if v not in (0,None,-1,0.0,'ffffffff'): out.append(f"{n}={v}")
    print("  ", ", ".join(out))
    vs = vtable.get(uid, {})
    for typ,off in sorted(vs.items()):
        raw=read_var(vdata,off); n=NF.get(typ,'?')
        print(f"   var {typ:5d} {n:20s} len={len(raw):3d} {decode_unicode(raw)!r:30.30} {raw[:24].hex()}")
