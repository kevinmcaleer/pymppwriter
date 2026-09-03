# pymppwriter

**Write Microsoft Project `.mpp` files from pure Python.** No Java, no .NET, no
Microsoft Project installation, no commercial library. MIT licensed.

pymppwriter produces native MPP14 files (Project 2010 through the current
Microsoft 365 desktop client). Files it writes open in Project by
double-click — which is the whole point: an `.mpp` download is associated with
Project on every corporate PC, whereas the MSPDI `.xml` export has to be
opened manually from inside Project.

Verified in Microsoft Project M365: tasks with durations, hierarchy and
dependencies; resources and assignments; calendars (custom weeks, holidays,
per-task calendars); notes, constraints, deadlines, percent complete, custom
fields; document metadata. Round-trip tested — files survive open → edit →
save in Project with stable task ids.

## Install

```bash
pip install pymppwriter
```

## The template (one-time)

pymppwriter works by **template-and-patch**: it keeps every stream of a
near-empty `.mpp` you save once from your own copy of Project, and rewrites
only the streams it understands. In Microsoft Project:

1. **File → New → Blank Project**
2. Add tasks `Task 1`, `Task 2`, `Task 3`
3. **Indent** Task 2 under Task 1; select Tasks 2 and 3 and **Link Tasks**
4. **Save As** `templates/template.mpp`

Save the template from the same Project version that will open the generated
files — several structures are stored in version-specific dialects.

## Quick start

```python
from datetime import datetime as D
from pymppwriter import MppWriter, Project, Task, Relation

project = Project(
    title="Robot build plan",
    start=D(2026, 10, 5, 8, 0),
    tasks=[
        Task(1, "Design",      D(2026, 10, 5, 8),  D(2026, 10, 9, 17),  duration_days=5),
        Task(2, "Print parts", D(2026, 10, 12, 8), D(2026, 10, 14, 17), duration_days=3),
    ],
    relations=[Relation(1, 2)],
)
MppWriter("templates/template.mpp").write(project, "robot-build.mpp")
```

Or from JSON on the command line:

```bash
pymppwriter build plan.json --template templates/template.mpp --out plan.mpp
```

See [Examples](examples.md) for specs covering every feature, and the
[format notes](FORMAT_NOTES.md) for what is known about the MPP file format.
