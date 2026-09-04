# mppwriter

**Read and write Microsoft Project `.mpp` files from TypeScript.** No Java, no .NET, no Project
install, no runtime dependencies. Works in Node and in the browser — the core takes bytes and
returns bytes.

A port of [pymppwriter](https://github.com/kevinmcaleer/pymppwriter), sharing its format notes and
its test fixtures. Both implementations are checked against each other byte for byte, so a fix in
one cannot silently diverge from the other.

> **Status: reading and writing both work.** Container, record layer, writer and reader are done.
> The writer produces files **byte-identical** to the Python implementation given the same input,
> and both readers return the same model from the same file — asserted on every test run. Publishing
> to npm is next; see [epic #54](https://github.com/kevinmcaleer/pymppwriter/issues/54).

## Install

```bash
npm install mppwriter
```

## Writing a plan

```ts
import { MppWriter } from "mppwriter";

const D = (y: number, m: number, d: number, h = 0) => new Date(Date.UTC(y, m - 1, d, h));

const bytes = new MppWriter(templateBytes).build({
  title: "Robot build",
  start: D(2027, 3, 1, 8),
  tasks: [
    { uid: 1, name: "Design", start: D(2027, 3, 1, 8), finish: D(2027, 3, 2, 17), durationDays: 2 },
    { uid: 2, name: "Build", start: D(2027, 3, 3, 8), finish: D(2027, 3, 5, 17), durationDays: 3 },
  ],
  relations: [{ predUid: 1, succUid: 2 }],
});
```

`build()` takes and returns bytes, so it runs unchanged in the browser — hand it a template from a
file input and download the result. **Dates are read in UTC**: the format stores wall-clock times
with no zone, so `Date.UTC(2027, 2, 1, 8)` means 08:00 in the plan.

Pass `newGuid` and `now` to make output reproducible, and `onWarning` to catch the schedules
Microsoft Project accepts but silently changes (a start earlier than its links allow, a start in
non-working time, a task calendar sharing no working time with its resources').

## Baselines

```ts
import { setBaseline, clearBaseline } from "mppwriter";

setBaseline(project);          // slot 0, the unnumbered Baseline
setBaseline(project, 3);       // Baseline3
clearBaseline(project, 0);
```

Saves the current schedule into one of the eleven slots, across all three entity classes:

| on a | baseline records |
|---|---|
| task | start, finish, duration and work, with summaries spanning their children |
| assignment | start, finish and work — the task's schedule scaled by the assignment's units |
| resource | work and cost, added up from its assignments (Project stores no dates here) |

They come back from `readProject()` as `task.baselines`, `resource.baselines` and
`assignment.baselines`, each keyed by slot. The timephased baseline blobs that Project uses only for
the usage views are not written — see `docs/FORMAT_NOTES.md` in the repository for why.

## Reading a plan back

```ts
import { readProject } from "mppwriter";

const project = readProject(bytes);          // any MPP14 file, 2010 through M365
for (const task of project.tasks) {
  console.log(task.uid, task.name, task.durationDays, task.percentComplete);
}
```

`readProject()` returns the same shape `build()` takes, so a file can be read, edited and written
again. Every offset comes from the file's own field maps, so it reads what any Project of that era
wrote, not just what this library produced. Baselines come back on tasks, resources and assignments;
costs and timephased data are not returned, as the writer does not model them either. A file that is
not an MPP14 project throws `MppReadError`.

## The container

```ts
import { readCfb, writeCfb, Storage } from "mppwriter";

const tree = readCfb(new Uint8Array(await file.arrayBuffer()));
console.log(tree.paths());                       // every stream in the file
const props = tree.get("   114/Props");          // a stream's bytes

const out = writeCfb(tree);                      // back to a .mpp container
```

`Storage` is an ordered tree of storages and streams. Children are held in a `Map`, never a plain
object — JavaScript reorders integer-like keys, and this format is full of numeric names.

## The record layer

```ts
import { parseProps, parseFieldMap, parseFixedMetaAuto, splitFixedData, PROPS_TASK_FIELD_MAP } from "mppwriter";

const { values } = parseProps(tree.get("   114/Props")!);
const fields = parseFieldMap(values.get(PROPS_TASK_FIELD_MAP)!);   // every offset comes from here
const meta = parseFixedMetaAuto(tree.get("   114/TBkndTask/FixedMeta")!, 47);
const records = splitFixedData(tree.get("   114/TBkndTask/FixedData")!, meta.items);
```

Field offsets are read from each file's own map rather than hard-coded, which is what lets one
implementation handle every MPP14-era Project. `encodeCp1252` is here too — JavaScript has no codec
for it, and the OLE property sets need one.

## Development

```bash
npm test        # node runs the TypeScript directly, no build step
npm run build   # dist/ with .d.ts declarations
```

Tests need no dependencies: Node's own test runner, and type stripping to run `.ts` sources. That
means **erasable syntax only** — no parameter properties, enums or decorators.

The parity test shells out to the Python implementation in the parent repo and compares bytes; it
skips when that is not present.
