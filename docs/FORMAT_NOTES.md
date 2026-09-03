# MPP14 format notes (dirty-room output)

Derived from: the public [MS-CFB] spec, the LGPL MPXJ reader's observable behaviour,
and byte-diffing of MPP14 files. No proprietary code was consulted.

## Container
OLE2 / MS-CFB v3: 512-byte sectors, 64-byte mini sectors, 4096-byte mini-stream cutoff.
Root entry CLSID = `74B78F3A-C8C8-11D1-BE11-00C04FB6FAF1` (Project). Project detects the
file by this CLSID + the `Props14` stream, not by extension.

```
\x01CompObj, \x05SummaryInformation (title/author), \x05DocumentSummaryInformation
Props14                      version header (492 bytes)
   114/                      "project" storage  (name is 3 spaces + 114)
   114/Props                 project properties (~85 KB) incl. FIELD MAPS (see below)
   114/TBkndTask/            tasks
   114/TBkndRsc/             resources
   114/TBkndAssn/            assignments
   114/TBkndCal/             calendars
   114/TBkndCons/            dependencies ("constraints" = links)
   114/TBkndOutlCode/        outline codes / lookup tables
   214/                      "view" storage: CV_iew, CTable, CFilter, CGrouping, CEdl, CReport, CMap, CUdm, CCommandBar, CVba, Props
```
Each `TBknd*` storage holds the same six streams:
`FixedMeta`, `FixedData`, `Fixed2Meta`, `Fixed2Data`, `VarMeta`, `Var2Data` (+ optional `Props`).

## Props stream
16-byte header: `uint32 (streamLen-4), uint32 (streamLen-4), uint32 unk, uint16 entryCount, uint16 unk`.
**Both size dwords must be updated when any entry changes length** — Project rejects the file otherwise.
Entries: `uint32 size, uint32 key, uint32 typeCode, bytes[size]`, 2-byte aligned. typeCode (0/2/4/9 observed)
**must be preserved** — Project rejects the file if it is zeroed. Type-0 strings are UTF-16LE + **4** NUL bytes.
Keys of interest:

| key       | meaning |
|-----------|---------|
| 131092    | TASK_FIELD_MAP (977 × 28-byte entries) |
| 131093    | RESOURCE_FIELD_MAP |
| 131094    | RELATION_FIELD_MAP |
| 131095    | ASSIGNMENT_FIELD_MAP |
| 37748738  | PROJECT_START_DATE (4-byte timestamp) |
| 37748744  | TITLE |

## Field map entry (28 bytes)
`+0 uint32 mask | +4 uint16 fixedOffset (65535 = not in fixed) | +6 byte varKey | +12 uint32 nativeFieldId | +20 uint16 category`

Native field ID = `0x0B40xxxx` (task), `0x0C40xxxx` (resource), `0x0F40xxxx` (assignment); low 16 bits index
`native_fields.json`. Category: `02` uint16, `03` int32, `05` double, `13` timestamp, `48` GUID, `65` double (work/cost),
`0B`/`64` = boolean stored as a bit in FixedMeta/Fixed2Meta (mask column), `08` string (in Var2Data).
A drop in fixedOffset marks the switch from FixedData (block 0) to Fixed2Data (block 1).
**The writer reads the template's own field map, so offsets are never hard-coded.**

## Fixed blocks
`FixedMeta`: `uint32 magic 0xFADFADBA, uint32, uint32 itemCount, uint32` then itemCount × N-byte items.
Item bytes +4..+8 = uint32 offset of the record in `FixedData`; the rest are per-record flag bits.
Item sizes: task 47 / 92 (meta2), resource 37 / 50–51, assignment 34, relation 10 / 10, calendar 10.
`FixedData` is the concatenation of records; record size = next offset − this offset.

Task record (block 0, 206 bytes in the reference) — key offsets from the field map:
`0 UID, 4 ID, 8 EarlyFinish, 12 LateStart, 24 FreeSlack, 36 ParentUID, 40 OutlineLevel(u16), 42 Duration, 46 DurationUnits,
52 RemainingDuration, 56 ConstraintType, 64 Start, 68 Finish, 72 ActualStart, 80 ConstraintDate, 88 Priority, 94 Type,
98 Created, 106 EarlyStart, 110 LateFinish, 126 Work(double), 150 Cost(double)`.
Block 1 (64 bytes): `0 GUID, 16 double position, 24 ParentGUID`. Summary flag = bit 0x20 of Fixed2Meta byte 8.
Deleted tasks are 16-byte stubs (`UID 0xFFFF0000+n`) kept at the front of the block.

## Var blocks
`VarMeta`: 24-byte header (`magic, 0, entryCount, 0, 0, var2DataSize`) then 12-byte entries `uint32 uid, uint32 offset, uint16 fieldLo, uint16 fieldHi`
where fieldHi is the class prefix (**0x0B40 tasks**, 0x0C40 resources, 0x0F40 assignments) — Project ignores the entry
(names vanish) if this is 0. Entries are sorted by (uid, field).
`Var2Data`: at each offset `uint32 size, bytes[size]`. Strings are UTF-16LE, NUL-terminated. Task name key = 14.
Project writes 10 var entries per task (baseline/deliverable placeholders); the writer clones them.

## Primitives
* Timestamp (4 bytes): `uint16 tenthsOfMinute, uint16 daysSince1983-12-31`; `FFFFFFFF` = null.
* Duration: int32 in tenths of a minute (1 day = 4800). Unit codes: 3 min, 5 h, 7 d, 9 w, 11 mo (+1 = elapsed).
* Work/cost: double, work in tenths of a minute.
* GUID: 16 bytes little-endian (Python `uuid.bytes_le`).

## Dependencies (TBkndCons)
FixedData record 20 bytes: `uint32 uid, uint32 predUID, uint32 succUID, uint16 type (0 FF,1 FS,2 SF,3 SS), uint16 lagUnits, int32 lag`.
Fixed2Data record 48 bytes: `GUID link, GUID pred, GUID succ`.

## Verified against Microsoft Project (M365, Sep 2026)
Container writer, task records, hierarchy, FS links, Props start date and title all open cleanly by double-click.
Open defect: Project shows every task as "1 day?" — duration at FixedData+42 is written correctly but not honoured;
suspect a flag in FixedMeta/Fixed2Meta marks the field as set. Being diagnosed by diffing a Project-resaved copy.

## Not yet handled
Resources, assignments (phantom per-task records exist in the template and must be regenerated or cleared),
calendars, notes, custom fields, SummaryInformation title, "next UID" counters in Props, baselines, timephased data.
