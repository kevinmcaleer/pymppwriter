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
| 16777217  | task record count (uint32, incl. deleted-task stubs and the uid-0 summary) |
| 16777218  | resource record count |
| 16777220  | assignment record count |
| 16777221  | relation record count |
| 37748738  | PROJECT_START_DATE (4-byte timestamp) |
| 37748744  | TITLE |

**The record counts must match the streams**: Project sizes its tables from them on load and
silently drops records beyond the count (verified in Project M365 — the last task vanished
while the count was one short).

## Field map entry (28 bytes)
`+0 uint32 mask | +4 uint16 fixedOffset (65535 = not in fixed) | +6 byte varKey | +12 uint32 nativeFieldId | +20 uint16 category`

Native field ID = `0x0B40xxxx` (task), `0x0C40xxxx` (resource), `0x0F40xxxx` (assignment); low 16 bits index
`native_fields.json`. Category: `02` uint16, `03` int32, `05` double, `13` timestamp, `48` GUID, `65` double (work/cost),
`0B`/`64` = boolean stored as a bit in FixedMeta/Fixed2Meta (mask column), `08` string (in Var2Data).
A drop in fixedOffset marks the switch from FixedData (block 0) to Fixed2Data (block 1).
**The writer reads the template's own field map, so offsets are never hard-coded.**

## Fixed blocks
`FixedMeta`: `uint32 magic 0xFADFADBA, uint32 4, uint32 itemCount, uint32 fixedDataByteLen` then
itemCount × N-byte items. **Both count and data-length dwords must be updated** when records change.
Item bytes +4..+8 = uint32 offset of the record in `FixedData`.
Item sizes vary by Project vintage — derive them from `(streamLen - 16) / itemCount` (exact division;
fall back to defaults when trailing slack makes it inexact). 2010-era: task 47 / 92 (meta2),
resource 37 / 50, assignment 34 / 53, relation 10 / **9**, calendar 10 / 9. M365: task Fixed2Meta
**96**, resource Fixed2Meta **51**, calendar Fixed2Meta **10**; the rest unchanged
(the 9-byte relation meta2 item is offset dword + one trailing byte, 0x07 observed).
`FixedData` is the concatenation of records; record size = next offset − this offset.

**Meta item bitmap** (the bytes after the offset dword): one bit per TASK_FIELD_MAP *entry*,
little-endian bit order, indexed by the entry's position in the field map. The task FixedMeta item
carries entries 0..311 ((47−8)×8), Fixed2Meta continues at 312. Boolean fields (category 0x0B/0x64 —
MILESTONE, SUMMARY, ESTIMATED, TASK_MODE, FLAG1-20…) store their **value** in their entry's bit; for
value fields the bit appears to mark the field as populated. Verified on three Project-written files
(entry order differs per file — Project reorders the field map — and the bits follow the entries:
e.g. NAME was entry 100 with bit 100 set in all files; SUMMARY at entry 10/35 matched summary rows;
MILESTONE at entry 5/17 matched milestone rows). Because entry order is file-specific, bit positions
must be derived from the file's own field map, never hard-coded.
Meta item bytes +0..+4: unknown flags (byte 2 varies per file: 0x0A–0x17 observed); cloned verbatim.

Task record (block 0, 206 bytes in the reference) — key offsets from the field map (file-specific;
heavily edited files lay these out differently):
`0 UID, 4 ID, 8 EarlyFinish, 12 LateStart, 24 FreeSlack, 36 ParentUID, 40 OutlineLevel(u16), 42 Duration,
46 ActualDurationUnits, 48 ActualDuration, 52 RemainingDuration, 56 ConstraintType, 64 Start, 68 Finish,
72 ActualStart, 80 ConstraintDate, 88 Priority, 94 Type, 98 Created, 106 EarlyStart, 110 LateFinish,
126 Work(double), 150 Cost(double)`.
The units word at +46 (ACTUAL_DURATION_UNITS): unit code 3 min / 5 h / 7 d / 9 w / 11 mo, +0x20 =
estimated (the "?" suffix); summary rows carry 0x15 instead of a unit code.
Block 1 (64 bytes): `0 GUID, 16 double position, 24 ParentGUID, 50 Start(rollup), 54 Finish(rollup),
58 ManualDuration, 62 ManualDurationUnits`. Summary/milestone/estimated flags are meta bitmap bits
(see above), not Fixed2Data bytes.
Manual vs auto scheduling: the flag M365 Project actually reads is **native id 1408's** bitmap bit
(1 = manually scheduled); the classic id 1280 "TASK_MODE" bit stays set in both modes. Auto tasks
carry -1/null in the block-1 manual start/finish/duration fields. M365 templates default new tasks
to manually scheduled, so a writer must clear 1408's bit explicitly. ESTIMATED (396) has no entry in
M365 field maps — the estimated flag lives in the units word alone there.
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
* Work: double in **thousandths of a minute** (1 day = 480000.0) — task and assignment records alike
  (verified against a Project-saved file; an earlier note claiming tenths was wrong). Cost: double.
* Percent-style values (max units, assignment units): double, 10000.0 = 100%.
* GUID: 16 bytes little-endian (Python `uuid.bytes_le`).

## Dependencies (TBkndCons)
FixedData record 20 bytes: `uint32 uid, uint32 predUID, uint32 succUID, uint16 type (0 FF,1 FS,2 SF,3 SS), uint16 lagUnits, int32 lag`.
Fixed2Data record 48 bytes: `GUID link, GUID pred, GUID succ`.

## Resources (TBkndRsc)
Layout from RESOURCE_FIELD_MAP (native class 0x0C40). Record ~188 bytes; key fields:
`0 UNIQUE_ID, 4 ID, 44 MAX_UNITS (double, 10000.0 = 100%), 28 STANDARD_RATE (double)`, plus the work/cost
doubles; block 1 (40 bytes): `0 GUID, 16 double position (row order, uid 0 = 1.0), 24 CALENDAR_GUID`.
Field id 56 (int32 at +16) = the uid of the resource's calendar record in TBkndCal.
Name (id 1), initials (2), email (35) are Var2Data strings keyed by native id.
Every project — even a blank one — contains 3 deleted stubs plus a full-size **uid-0 "Unassigned"
resource record**, which is the writer's prototype. A resource's GUID, its CALENDAR_GUID and its
calendar record's GUID are all the same GUID.

## Assignments (TBkndAssn)
Layout from ASSIGNMENT_FIELD_MAP (0x0F40). FixedData record 110 bytes:
`0 UNIQUE_ID, 4 TASK_UNIQUE_ID, 8 RESOURCE_UNIQUE_ID (int32; 0xFFFF0001 = unassigned), 12 Start, 16 Finish,
20 Resume, 36 Stop, 46 ASSIGNMENT_UNITS (double, 10000.0 = 100%), 54 WORK (double, minutes × 1000 =
duration tenths × 100), 70 REGULAR_WORK, 78 REMAINING_WORK`.
Fixed2Data record 48 bytes: `GUID assignment, GUID task, GUID resource` — the join keys Project uses.
Meta item sizes 34 / 53. **Project schedules an assigned task from its assignment records** (joined by
task unique id), *not* from the task's own duration/work fields — which is why the template's phantom
unassigned records (one per task, all 1 day) must never be cloned unpatched. On save Project recreates
one phantom (resource uid 0xFFFF0001, units 100%, work = task duration × 100) per unassigned task, and
an all-zero assignment uid 1 for the project summary; omitting them is tolerated.
Var entries per assignment: keys 16, 32 (baseline work/cost), 146, 147 (baseline start/finish),
267, 665, 634 = creation timestamp, and **49 = the planned-work contour Project actually schedules
from**: `+8 double = units × 16` (160000.0 at 100%), `+16 double = total work (milli-minutes)`,
`+24 uint32 = elapsed assignment duration in tenths × 8`, `+32 uint32 = contour block count (1)`.
Getting +24 wrong (e.g. work×0.08, which coincides at 100% units) makes Project display and reschedule
the task to the wrong duration.

## Calendars (TBkndCal)
FixedData: 16-byte deleted stubs, then 12-byte records of three int32 columns: calendar uid, base
calendar uid, owning resource uid (-1s — or 0 for the base column in fresh M365 files — for a base
calendar like Standard). **Column order varies by Project version** — (uid, base, resource) in
2010-era files, (base, resource, uid) in M365 files — so detect it from the uid-0 resource's calendar
row: its three values (its own calendar uid from the resource record's CALENDAR_UID field, the
Standard uid, and resource uid 0) are distinct and identify every column.
Fixed2Data record 48 bytes: `GUID calendar (= the owning resource's GUID), GUID calendar again, GUID of
the base calendar` (zeros for Standard's own row beyond the first). Meta items 10 / 9-10 bytes.
Each resource needs its own calendar record (base = Standard); new base calendars use -1 for both the
base and resource columns.

Var2Data per calendar: **key 1 = name** (UTF-16), **key 8 = the definition blob**. A calendar with no
blob uses Project's built-in defaults (Mon-Fri 08:00-12:00, 13:00-17:00). Blob layout (the dialect
Project M365 writes and reads; an older form using day type 2 for working days is readable by MPXJ
but **ignored by Project**):
* 7 × 60-byte day blocks, **Sunday first**: `uint16 dayType (1 = default, 0 = explicit — working vs
  non-working decided by the range count), uint16 rangeCount, uint32 total working tenths at +4`,
  range start times as uint16 tenths-of-a-minute from midnight at +8 (stride 2), range durations as
  uint32 tenths at +20 (stride 4) and cumulative durations at +40. Up to 5 ranges.
* then optionally `uint32 exceptionCount` and per exception: a 92-byte record — `uint16 fromDay,
  uint16 toDay` (days since 1983-12-31), `uint16 dayCount`, recurrence dwords `1, 0, 1, 0x4000` at
  +72 (zeroes make readers reject the exception), `uint32 nameByteLen` at +88 — followed by the
  UTF-16 name, zero-padded so the next record starts 4-byte aligned, with 4 closing zero bytes after
  the last. Exceptions are sorted by date; only non-working exceptions are understood so far.

**Three gates decide whether Project reads a base calendar's blob at all** (each was found the hard
way; without any one of them the file opens but shows default working time):
1. the record's meta item: flags byte 2 = the record's var entry count, and the trailing byte must
   carry the has-data flag — **0x80 in 2010-era metas, 0x40 in M365 ones** (name-plus-data is 0xCF
   in both, so OR 0xC0);
2. Props key 65539 (0x10003) = the TBkndCal Var2Data byte length — Project truncates its var-data
   read at the declared length (keys 65537/65538/65540 do the same for tasks/resources/assignments);
3. Props key 8388609 (0x800001) = count of base calendars with custom working time (resource-calendar
   blobs load regardless of it).
The default project calendar is referenced **by name** in Props key 37748750 (UTF-16 + 4 NUL).
A task's calendar is CALENDAR_UNIQUE_ID (native id 401, int32) in its fixed record plus the presence
bit; -1/absent = project default. Props key 37753736 (0x2401388) holds a static 420-byte default-week
definition (identical in every file observed; not where edits go).

## Verified against Microsoft Project (M365, Sep 2026)
Container writer, task records, hierarchy, FS links, Props start date and title all open cleanly by double-click.
Durations verified correct, including display units (days/weeks), the estimated "?" flag, 0-day milestones and
working-time summary rollups.

The "every task shows 1 day?" defect had three causes, found by A/B tests in Project:
1. **Phantom assignments**: the template's TBkndAssn records (one per template task, 1 day of work each) are
   joined to tasks by task unique id, and Project overrides the task's duration from the assignment. MPXJ
   ignores them, which is why the MPXJ oracle always read the written durations correctly while Project did
   not. The writer now empties TBkndAssn.
2. **Stale record counts** in Props (see table above) made Project drop task records beyond the template's
   count and load the template's relation/assignment counts.
3. The units word / estimated flag and the boolean meta-bitmap bits (milestone/summary/estimated) had to be
   written per task — Project honours them exactly as described above.

## Project properties and document metadata
Props stream keys: 0x2400002 project start, **0x2400003 project finish**, 0x2400006 creation date,
0x2400007 last-saved date, **0x2400045 status date** (0xFFFFFFFF = NA; located by fuzzing every NA
key with distinct timestamps and reading the result back), 0x2400010 currency symbol and 0x24013BB
currency code (UTF-16 short strings), 37748750 default calendar name.
**0x24000AE (2010-era only)** holds legacy next-unique-id counters; stale values make Project
renumber task uids on open (observed: uid 10 → 7), and M365 deletes the key on save — so the writer
drops it. M365 files carry no next-uid counters at all: Project derives them from the loaded records.
Document metadata lives in the root `\x05SummaryInformation` property set (MS-OLEPS, VT_LPSTR
cp1252): pid 2 title, 3 subject, 4 author, 5 keywords, 6 comments, 8 last author, 12/13 created/saved
FILETIMEs — and `\x05DocumentSummaryInformation` section 0: pid 14 manager, 15 company, 2 category.
`Props14` (root stream) is the provenance/version block: length-prefixed UTF-16 entries with the
creating and saving application versions ("16,0,20326,20112" = M365; "14,0,4751,1000" = Project
2010), the username and the original file title. Kept verbatim from the template — it is what
declares the file's era, which several structures' dialects key off.

## Round-trip fidelity
Two engine behaviours matter when Project recalculates an opened file:
* an auto-scheduled ASAP task snaps back to its earliest date, so the writer pins declared starts
  with a Start-No-Earlier-Than constraint — the same thing Project does when a user types a start
  date. Only dates the links do not already produce are pinned: `link_driven_start()` works out
  where each predecessor puts its successor (FS to the next working moment after the finish, or
  onto the finish itself for a zero-duration milestone; SS to the predecessor's start; FF and SF
  drive the finish, not the start), and a task sitting exactly there is left ASAP so the plan stays
  link-driven the way a hand-built one is;
* the unmapped relation-record trailer moved between eras: 2010 files use `type@12, lagUnits@14,
  lag@16`, M365 files `type@12, lag@14, lagUnits@18` (writing the 2010 shape into an M365 file put
  the units code 7 into the lag field = 42-second successor slips). The era is detected from the
  relation field map (native id 9 at offset 0 = 2010).
`scripts/roundtrip_check.py` compares a generated file against a Project-resaved copy through MPXJ
(tasks, resources, assignments; rows added in Project are allowed) — both findings above came out of
its first run. The Gantt scroll position is a timestamp inside `214/CV_iew/Var2Data` equal to the
template's project start; the writer retargets it to the new start so files open on the schedule.
Two disagreements with the scheduling engine are caught before Project sees them, as
`ScheduleWarning`s: a declared start *earlier* than the links allow (Project moves it on the next
recalculation — no constraint can hold a task before its predecessor), and a task calendar sharing
no working time with its assigned resources' calendars (Project opens with "Not enough common
working time" and schedules the task ignoring the resource calendar).
Templates of different Project vintages are handled by deriving meta item sizes from stream headers,
reading every layout from the file's own field maps, and era-detecting the calendar record columns,
calendar flag bits and the relation trailer; 2010-era and current M365 templates are round-trip
tested, and 2013-2021 files share the same MPP14 structures between those two points. Point the
suite at any other vintage with `PYMPP_TEMPLATES=/path/a.mpp:/path/b.mpp pytest`.

## Not yet handled
Resource rates and costs, material and cost resource types, per-resource working weeks (resource
calendars are written as copies of Standard), assignment actual work / percent complete, baselines,
timephased data, and subprojects.
