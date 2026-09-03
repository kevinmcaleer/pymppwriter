"""Golden-file round-trip check: does a Project-resaved copy still describe
the same schedule as the file we generated?

    python scripts/roundtrip_check.py generated.mpp resaved-by-project.mpp

Reads both through MPXJ and compares tasks (uid, name, start, finish,
duration), resources (uid, name, max units), and assignments (task, resource,
units). Tasks/resources that exist only in the resaved file are reported but
allowed (the user may have added rows in Project before saving); anything
missing or changed fails. Requires Java + `pip install mpxj jpype1`.
"""
import sys

import jpype
import jpype.imports
import mpxj  # noqa: F401  (sets up the JVM classpath)

jpype.startJVM()
from org.mpxj.reader import UniversalProjectReader  # noqa: E402


def snapshot(path):
    pf = UniversalProjectReader().read(path)
    tasks = {}
    for t in pf.getTasks():
        uid = t.getUniqueID().intValue()
        tasks[uid] = (str(t.getName()), str(t.getStart()), str(t.getFinish()),
                      str(t.getDuration()))
    resources = {}
    for r in pf.getResources():
        resources[r.getUniqueID().intValue()] = (str(r.getName()), str(r.getMaxUnits()))
    assignments = set()
    for a in pf.getResourceAssignments():
        if a.getResourceUniqueID() is not None and a.getResourceUniqueID().intValue() > 0:
            assignments.add((a.getTaskUniqueID().intValue(),
                             a.getResourceUniqueID().intValue(), str(a.getUnits())))
    return tasks, resources, assignments


def main():
    a, b = sys.argv[1], sys.argv[2]
    ta, ra, aa = snapshot(a)
    tb, rb, ab = snapshot(b)
    failures = []
    for uid, row in ta.items():
        if uid not in tb:
            failures.append(f"task uid {uid} {row[0]!r} missing from resave")
        elif tb[uid] != row:
            failures.append(f"task uid {uid} changed: {row} -> {tb[uid]}")
    for uid in tb.keys() - ta.keys():
        print(f"note: task uid {uid} {tb[uid][0]!r} added in Project (allowed)")
    for uid, row in ra.items():
        if uid == 0:
            continue
        if uid not in rb:
            failures.append(f"resource uid {uid} {row[0]!r} missing from resave")
        elif rb[uid] != row:
            failures.append(f"resource uid {uid} changed: {row} -> {rb[uid]}")
    for key in aa - ab:
        failures.append(f"assignment {key} missing from resave")
    if failures:
        print(f"ROUND-TRIP FAILED ({len(failures)}):")
        for f in failures:
            print("  " + f)
        sys.exit(1)
    print(f"round-trip OK: {len(ta)} tasks, {len(ra)} resources, {len(aa)} assignments preserved")


if __name__ == "__main__":
    main()
