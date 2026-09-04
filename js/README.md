# mppwriter

**Read and write Microsoft Project `.mpp` files from TypeScript.** No Java, no .NET, no Project
install, no runtime dependencies. Works in Node and in the browser — the core takes bytes and
returns bytes.

A port of [pymppwriter](https://github.com/kevinmcaleer/pymppwriter), sharing its format notes and
its test fixtures. Both implementations are checked against each other byte for byte, so a fix in
one cannot silently diverge from the other.

> **Status: in progress.** The compound-file container and the record layer — Props, field maps,
> fixed records and their meta bitmaps, var blocks, OLE property sets, calendar data — are done and
> byte-identical to the Python implementation. The writer and reader are next; see
> [epic #54](https://github.com/kevinmcaleer/pymppwriter/issues/54).

## Install

```bash
npm install mppwriter
```

## The container today

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
