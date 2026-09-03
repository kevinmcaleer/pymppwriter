# pymppwriter

**Write Microsoft Project `.mpp` files from pure Python.** No Java, no .NET, no Microsoft Project
installation, no commercial library. MIT licensed.

`pymppwriter` produces native MPP14 files (the format used by Project 2010 through the current
Microsoft 365 desktop client). Files it writes open in Project by double-click — which is the
whole point: an `.mpp` download is associated with Project on every corporate PC, whereas the
MSPDI `.xml` export has to be opened manually from inside Project.

> **Status: alpha.** Task names, hierarchy, dates, durations (values, display units, estimated
> flags, milestones and summary rollups), dependencies and the project start date are verified
> to open correctly in Project M365. Resources (name, initials, email, max units, per-resource
> calendars) and assignments (units, work, GUID cross-references) are written and verified via
> MPXJ. Rates/costs, calendar edits, notes and custom fields are not yet written. Treat this
> as a working proof-of-concept, not a product.

## How it works

The MPP format is an undocumented OLE2 compound document. Reading it selectively is a solved
problem (see [MPXJ](https://www.mpxj.org)); writing one from scratch means reproducing ~170 KB
of view definitions, Gantt bar styles, tables and filters that Project insists on.

`pymppwriter` sidesteps that with a **template-and-patch** approach:

1. You save a near-empty project from your own copy of Project once (`templates/template.mpp`).
2. The library keeps every stream it doesn't understand byte-for-byte.
3. It rewrites only the task, dependency and project-property streams, cloning prototype
   records from the template and patching the fields it controls.

The field-offset map is read from the template's own `Props` stream, so the writer adapts to
whatever Project version wrote the template. Details in [`docs/FORMAT_NOTES.md`](docs/FORMAT_NOTES.md).

## Installation

```bash
pip install git+https://github.com/kevinmcaleer/pymppwriter
```

Only runtime dependency: [`olefile`](https://pypi.org/project/olefile/) (used to *read* the
template; the container writer is our own).

## Make your template (one-time)

In Microsoft Project: **File → New → Blank Project**, then

| # | Task Name | Action |
|---|-----------|--------|
| 1 | Task 1 | leave as-is |
| 2 | Task 2 | **Indent** it under Task 1 (Task 1 becomes a summary) |
| 3 | Task 3 | select Tasks 2 and 3, **Link Tasks** (Finish-to-Start) |

Don't add resources, baselines or calendar changes. **Save As** `templates/template.mpp`.

Why you must make it yourself: the template is a file written by Project, so it must come from
a copy you're licensed to use, and it embeds your username. It is `.gitignore`d.

## Usage

### Command line

Describe the plan in JSON ([`examples/example_project.json`](examples/example_project.json)):

```json
{
  "title": "My plan",
  "start": "2026-09-07T08:00",
  "tasks": [
    {"uid": 1, "name": "Phase 1", "start": "2026-09-07T08:00", "finish": "2026-09-09T17:00", "duration_days": 3, "outline_level": 1},
    {"uid": 2, "name": "Do the thing", "start": "2026-09-07T08:00", "finish": "2026-09-08T17:00", "duration_days": 2, "outline_level": 2, "parent_uid": 1}
  ],
  "links": [ {"pred": 1, "succ": 2, "type": "FS", "lag_days": 0} ]
}
```

```bash
pymppwriter build examples/example_project.json --template templates/template.mpp --out plan.mpp
pymppwriter inspect plan.mpp        # dump the OLE stream tree
```

### Python API

```python
from datetime import datetime as D
from pymppwriter import MppWriter, Project, Task, Relation

project = Project(
    title="Robot build plan",
    start=D(2026, 10, 5, 8, 0),
    tasks=[
        Task(uid=1, name="Design",      start=D(2026,10,5,8),  finish=D(2026,10,9,17),  duration_days=5),
        Task(uid=2, name="Print parts", start=D(2026,10,12,8), finish=D(2026,10,14,17), duration_days=3),
        Task(uid=3, name="Assemble",    start=D(2026,10,15,8), finish=D(2026,10,16,17), duration_days=2,
             outline_level=1, parent_uid=0),
    ],
    relations=[Relation(1, 2), Relation(2, 3, type="FS", lag_days=0)],
)

MppWriter("templates/template.mpp").write(project, "robot-build.mpp")
```

**Model reference**

| Class | Field | Notes |
|-------|-------|-------|
| `Project` | `title`, `start`, `tasks`, `relations` | `start` sets the project start date |
| `Task` | `uid` | unique, > 0, stable across exports |
| | `name`, `start`, `finish` | `datetime`s |
| | `duration_days` | working days; 0 = milestone; ignored for summary tasks (rolled up from children in working time) |
| | `duration_units` | display units: `"m"`, `"h"`, `"d"` (default), `"w"`, `"mo"` |
| | `estimated` | `True` shows the duration with a trailing `?` |
| | `outline_level` | 1 = top level, 2 = child, … |
| | `parent_uid` | 0 = top level, else uid of the summary task |
| | `guid` | auto-generated; pass your own to keep GUIDs stable between exports |
| `Relation` | `pred_uid`, `succ_uid` | |
| | `type` | `"FS"` (default), `"SS"`, `"FF"`, `"SF"` |
| | `lag_days` | may be negative for lead |
| `Resource` | `uid` | unique, > 0 |
| | `name`, `initials`, `email` | strings; only `name` is required |
| | `max_units` | 1.0 = 100% (default) |
| | `guid` | auto-generated; pass your own to keep GUIDs stable |
| `Assignment` | `task_uid`, `resource_uid` | must reference existing tasks/resources |
| | `units` | 1.0 = 100% (default); work is computed from the task's duration |

In JSON specs, resources and assignments look like:

```json
"resources": [ {"uid": 1, "name": "Kevin", "initials": "K", "max_units": 1.0} ],
"assignments": [ {"task": 1, "resource": 1, "units": 0.5} ]
```

Tasks are written in list order, which becomes the ID / row order in Project.

## Verifying output without Project

If you have Java installed, `scripts/mpxj_oracle.py` reads any `.mpp` back through
[MPXJ](https://www.mpxj.org) (`pip install mpxj jpype1`) and prints tasks, links and
resources. `scripts/analyze_mpp.py` dumps the task records field-by-field — useful when
diffing against a file Project saved.

## Development

```bash
git clone https://github.com/kevinmcaleer/pymppwriter && cd pymppwriter
pip install -e ".[dev]"
pytest
```

The end-to-end test is skipped unless `templates/template.mpp` exists.

## Roadmap

Tracked in the [GitHub Project](../../projects). Headline epics:

1. ~~**Durations honoured by Project**~~ — done (verified in Project M365)
2. **Resources & assignments** — the template's phantom assignment records are now cleared; writing real ones is next
3. **Calendars** — project calendar and per-task calendar
4. **Notes, custom fields, WBS, constraints, deadlines**
5. **Round-trip fidelity** — `Save` from Project after opening produces an identical schedule
6. **NoodlePlanner integration** — markdown → `.mpp` export

## Provenance & licensing

All format knowledge comes from the public [MS-CFB] specification, the observable read behaviour
of the LGPL MPXJ library, and byte-diffing files saved by Microsoft Project. No code was
derived from MPXJ or from any proprietary library, and no proprietary binaries were decompiled.
The repository ships **no** `.mpp` files: MPXJ's test fixtures are LGPL and were used only as
read-only references during development.

Microsoft Project is a trademark of Microsoft Corporation. This project is not affiliated with
Microsoft.
