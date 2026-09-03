# Templates

`pymppwriter` works by cloning a small `.mpp` saved by Microsoft Project and rewriting the
task, link and project-property streams. **You must supply this file yourself** — it must be
written by a copy of Project you are licensed to use, and it must contain:

1. Task 1 (a summary task)
2. Task 2, indented under Task 1
3. Task 3, linked Finish-to-Start after Task 2

Nothing else: no resources, no baseline, no calendar edits. Save it as `templates/template.mpp`.

The library reads the field-offset map from the template's own `Props` stream, so it adapts to
the exact Project version that wrote it. `.mpp` files are `.gitignore`d because a file saved by
Project may contain your user name and machine path.
