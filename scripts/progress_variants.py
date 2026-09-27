"""Verification matrix for #56: a 100%-complete task with an assignment opens in
Microsoft Project at 99% with a zero duration.

Only Project itself can judge these files — MPXJ reads the fixed fields at face
value and never reconciles them against the timephased contours the way
Project does. Two modes:

    python scripts/progress_variants.py build [template.mpp] [out_dir]
        writes one file per candidate encoding (A-F below). Open each in
        Project and note, for task 1 "Assigned, done": % Complete, Duration,
        Finish, and whether a dialog appears on open.

    python scripts/progress_variants.py dump file.mpp
        prints every assignment's fixed fields and var entries 49/50 in full.
        Run it on a file where *Project* marked an assigned task 100% complete:
        those bytes are the ground truth every variant is guessing at.
"""
import json
import os
import struct
import sys
from datetime import datetime as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pymppwriter import MppWriter, Project, Task, Resource, Assignment  # noqa: E402
from pymppwriter import blocks as B  # noqa: E402
from pymppwriter.cfb import load_cfb  # noqa: E402
from pymppwriter.writer import ASSN_META_SIZE, ASSN_PROGRESS_DEFAULTS, PRJ  # noqa: E402

# name -> (what it tests, overrides of ASSN_PROGRESS_DEFAULTS)
VARIANTS = {
    "A": ("control: what 0.4.1 ships", {}),
    "B": ("assignment actual start/finish/% work complete, contours as shipped",
          {"assn_actuals": True}),
    "C": ("B + actual-work contour (var 50), remaining contour zeroed",
          {"assn_actuals": True, "actual_contour": True}),
    "D": ("C with the remaining contour's count word 0",
          {"assn_actuals": True, "actual_contour": True, "remaining_at_100": "count0"}),
    "E": ("D without the assignment actuals (isolates them against D)",
          {"actual_contour": True, "remaining_at_100": "count0"}),
    "F": ("B with the full pre-0.4.1 remaining contour",
          {"assn_actuals": True, "remaining_at_100": "full"}),
}


def sample_project() -> Project:
    return Project(
        "progress-variants", D(2026, 9, 7, 8),
        [Task(1, "Assigned, done", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2,
              percent_complete=100),
         Task(2, "Unassigned, done (control)", D(2026, 9, 9, 8), D(2026, 9, 10, 17),
              duration_days=2, percent_complete=100),
         Task(3, "Assigned, half done", D(2026, 9, 11, 8), D(2026, 9, 14, 17), duration_days=2,
              percent_complete=50)],
        resources=[Resource(1, "Tester")],
        assignments=[Assignment(1, 1), Assignment(3, 1)])


def build(template: str, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for name, (what, overrides) in VARIANTS.items():
        w = MppWriter(template)
        w._assn_progress = dict(ASSN_PROGRESS_DEFAULTS, **overrides)
        path = os.path.join(out_dir, f"variant-{name}.mpp")
        w.write(sample_project(), path)
        print(f"{path}  {what}")
    print("\nFor each file, in Project: task 1's % Complete, Duration and Finish, and any "
          "dialog on open.\nExpected: 100%, 2 days, finishing Tue 2026-09-08 17:00.")


def dump(path: str) -> None:
    names = {int(k): v for k, v in json.load(open(os.path.join(
        os.path.dirname(__file__), "..", "pymppwriter", "native_fields.json")))["assignment"].items()}
    root = load_cfb(path)

    def get(p):
        node = root
        for part in p.split("/"):
            node = node.children[part]
        return node

    _, props, _ = B.parse_props(get(f"{PRJ}/Props"))
    fields = [it for it in B.parse_field_map(props[B.PROPS_ASSIGNMENT_FIELD_MAP]) if it.in_fixed]
    a = f"{PRJ}/TBkndAssn"
    _, _, mitems = B.parse_fixed_meta_auto(get(f"{a}/FixedMeta"), ASSN_META_SIZE)
    recs = B.split_fixed_data(get(f"{a}/FixedData"), mitems)
    _, _, m2items = B.parse_fixed_meta_auto(get(f"{a}/Fixed2Meta"), 53)
    recs2 = B.split_fixed_data(get(f"{a}/Fixed2Data"), m2items)
    _, vtable, _ = B.parse_var_meta(get(f"{a}/VarMeta"))
    vdata = get(f"{a}/Var2Data")
    for i, rec in enumerate(recs):
        if len(rec) <= 50:
            continue
        uid = struct.unpack_from("<I", rec, 0)[0]
        print(f"\nASSIGNMENT uid={uid}")
        for it in sorted(fields, key=lambda f: (f.block, f.offset)):
            src = rec if it.block == 0 else (recs2[i] if i < len(recs2) else b"")
            tid = it.type_value & 0xFFFF
            if it.offset + 4 > len(src):
                continue
            if it.category == 0x13:
                v = B.decode_timestamp(src, it.offset)
            elif it.category in (0x65, 0x05) and it.offset + 8 <= len(src):
                v = struct.unpack_from("<d", src, it.offset)[0]
            elif it.category == 0x02:
                v = struct.unpack_from("<H", src, it.offset)[0]
            elif it.category == 0x03:
                v = struct.unpack_from("<i", src, it.offset)[0]
            else:
                v = src[it.offset:it.offset + 4].hex()
            print(f"  fixed {tid:4d} {names.get(tid, '?'):40s} {v}")
        for typ, off in sorted(vtable.get(uid, {}).items()):
            raw = B.read_var(vdata, off)
            shown = raw.hex() if typ in (49, 50) else raw[:24].hex()
            print(f"  var   {typ:4d} {names.get(typ, '?'):40s} len={len(raw):3d} {shown}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        build(sys.argv[2] if len(sys.argv) > 2 else "templates/template.mpp",
              sys.argv[3] if len(sys.argv) > 3 else "progress-variants")
    elif len(sys.argv) == 3 and sys.argv[1] == "dump":
        dump(sys.argv[2])
    else:
        sys.exit(__doc__)
