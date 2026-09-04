"""Read a project with the Python implementation and print it as JSON.

The TypeScript parity test reads the same file and compares the models, so the
two readers cannot drift apart.
"""
import json
import sys

sys.path.insert(0, sys.argv[1])          # the repo root
from pymppwriter import read_project     # noqa: E402

p = read_project(sys.argv[2])
iso = lambda d: d.isoformat() if d else None   # noqa: E731
print(json.dumps({
    "title": p.title,
    "start": iso(p.start),
    "tasks": [{
        "uid": t.uid, "name": t.name, "start": iso(t.start), "finish": iso(t.finish),
        "durationDays": t.duration_days, "outlineLevel": t.outline_level,
        "parentUid": t.parent_uid, "durationUnits": t.duration_units,
        "estimated": t.estimated, "notes": t.notes, "wbs": t.wbs,
        "constraint": t.constraint, "constraintDate": iso(t.constraint_date),
        "percentComplete": t.percent_complete, "priority": t.priority, "manual": t.manual,
    } for t in p.tasks],
    "relations": [{"predUid": r.pred_uid, "succUid": r.succ_uid, "type": r.type,
                   "lagDays": r.lag_days} for r in p.relations],
    "resources": [{"uid": r.uid, "name": r.name, "initials": r.initials, "email": r.email,
                   "maxUnits": r.max_units} for r in p.resources],
    "assignments": [{"taskUid": a.task_uid, "resourceUid": a.resource_uid, "units": a.units}
                    for a in p.assignments],
}, indent=None))
