"""Golden-file round-trip check: does a Project-resaved copy still describe
the same schedule as the file we generated?

    python scripts/roundtrip_check.py generated.mpp resaved-by-project.mpp

Reads both through MPXJ and compares tasks (uid, name, start, finish,
duration, constraint), baselines on tasks, resources and assignments (all
eleven slots), links (predecessor, successor, type, lag), resources (uid,
name, max units), and assignments (task, resource, units).
Tasks/resources that exist only in the resaved file are reported but allowed
(the user may have added rows in Project before saving); anything missing or
changed fails. Requires Java + `pip install mpxj jpype1`.
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
    baselines = {}
    links = set()
    for t in pf.getTasks():
        uid = t.getUniqueID().intValue()
        tasks[uid] = (str(t.getName()), str(t.getStart()), str(t.getFinish()),
                      str(t.getDuration()), str(t.getConstraintType()),
                      str(t.getConstraintDate()))
        # baselines: slot 0 is the unnumbered one, 1-10 the numbered slots.
        # A saved baseline that a resave drops is exactly the kind of silent
        # loss this check exists to catch.
        for slot in range(11):
            if slot == 0:
                start, finish, dur = t.getBaselineStart(), t.getBaselineFinish(), t.getBaselineDuration()
            else:
                start = t.getBaselineStart(slot)
                finish = t.getBaselineFinish(slot)
                dur = t.getBaselineDuration(slot)
            if start is None and finish is None:
                continue
            baselines[("task", uid, slot)] = (str(start), str(finish), str(dur))
        for r in t.getPredecessors():
            links.add((r.getPredecessorTask().getUniqueID().intValue(), uid,
                       str(r.getType()), str(r.getLag())))
    resources = {}
    for r in pf.getResources():
        uid = r.getUniqueID().intValue()
        resources[uid] = (str(r.getName()), str(r.getMaxUnits()))
        # A resource baseline is work and cost only — Project stores no dates.
        # MPXJ reports an unset slot as 0.0h rather than None, and a cleared
        # slot is likewise all zeros, so only a non-zero slot counts as saved.
        for slot in range(11):
            work = r.getBaselineWork() if slot == 0 else r.getBaselineWork(slot)
            cost = r.getBaselineCost() if slot == 0 else r.getBaselineCost(slot)
            if not (work and work.getDuration()) and not (cost and cost.doubleValue()):
                continue
            baselines[("resource", uid, slot)] = (str(work), str(cost))
    assignments = set()
    for a in pf.getResourceAssignments():
        rsc_uid = a.getResourceUniqueID()
        if rsc_uid is None or rsc_uid.intValue() <= 0:
            continue
        key = (a.getTaskUniqueID().intValue(), rsc_uid.intValue())
        assignments.add(key + (str(a.getUnits()),))
        for slot in range(11):
            if slot == 0:
                start, finish = a.getBaselineStart(), a.getBaselineFinish()
                work = a.getBaselineWork()
            else:
                start, finish = a.getBaselineStart(slot), a.getBaselineFinish(slot)
                work = a.getBaselineWork(slot)
            if start is None and finish is None:
                continue
            baselines[("assignment", key, slot)] = (str(start), str(finish), str(work))
    return tasks, baselines, links, resources, assignments


def main():
    a, b = sys.argv[1], sys.argv[2]
    ta, bla, la, ra, aa = snapshot(a)
    tb, blb, lb, rb, ab = snapshot(b)
    failures = []
    for uid, row in ta.items():
        if uid not in tb:
            failures.append(f"task uid {uid} {row[0]!r} missing from resave")
        elif tb[uid] != row:
            failures.append(f"task uid {uid} changed: {row} -> {tb[uid]}")
    for uid in tb.keys() - ta.keys():
        print(f"note: task uid {uid} {tb[uid][0]!r} added in Project (allowed)")
    for key, row in bla.items():
        kind, uid, slot = key
        name = "Baseline" if slot == 0 else f"Baseline{slot}"
        if key not in blb:
            failures.append(f"{name} on {kind} {uid} missing from resave")
        elif blb[key] != row:
            failures.append(f"{name} on {kind} {uid} changed: {row} -> {blb[key]}")
    for key in la - lb:
        pred, succ, kind, lag = key
        near = [k for k in lb if k[:2] == (pred, succ)]
        failures.append(f"link {pred} -> {succ} {kind} lag {lag} "
                        + (f"changed to {near[0][2]} lag {near[0][3]}" if near else "missing from resave"))
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
    print(f"round-trip OK: {len(ta)} tasks, {len(bla)} baselines, {len(la)} links, "
          f"{len(ra)} resources, {len(aa)} assignments preserved")


if __name__ == "__main__":
    main()
