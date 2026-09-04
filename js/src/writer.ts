/**
 * Template-based MPP14 writer.
 *
 * Start from a minimal `.mpp` saved by Microsoft Project (the template), keep
 * every stream we do not understand untouched, and regenerate only the streams
 * we do — cloning prototype records from the template and patching the fields
 * we control. A port of `writer.py`, kept structurally identical so the two
 * produce the same bytes.
 */
import { Storage, readCfb, writeCfb } from "./cfb.ts";
import * as B from "./blocks.ts";
import {
  ASSN_BASELINE_BUDGET, ASSN_BASELINE_IDS, ASSN_META_SIZE, ASSN_NATIVE, ASSN_VAR_EMPTY,
  BASELINE_UNSET_DOUBLE, CAL_DATA_VAR, CAL_META_SIZE, CAL_NAME_VAR,
  CONSTRAINT_TYPES, DATE_IDS, ESTIMATED_FLAG, FLAG_IDS, NATIVE, NULL_RESOURCE_GUID,
  NULL_RESOURCE_UID, NUMBER_IDS, PCT_SCALE, PRJ, PROJECT_CLSID, PROPS_BASELINE_SAVED,
  REL_META2_SIZE, REL_META_SIZE,
  REL_TYPES, RSC_BASELINE_BUDGET, RSC_BASELINE_IDS, RSC_META_SIZE, RSC_NATIVE, ScheduleWarning,
  SUMMARY_UNITS, TASK_BASELINE_EXTRAS, TASK_BASELINE_IDS, TASK_META2_SIZE,
  TASK_META_SIZE, TASK_TYPES, TENTHS_PER_DAY, TEXT_IDS, UNITS_CODES, WORK_SCALE,
  advanceWorking, baselineSlots, effectiveSchedule, encodeRtfNotes, linkDrivenStart,
  nextWorkingMoment, previousWorkingMoment,
  validate, weeklyOverlapMinutes, workPattern, workingTenths,
  type Assignment, type Baseline, type Calendar, type Project, type Relation, type Resource,
  type Task, type WorkPattern,
} from "./model.ts";

declare const crypto: { getRandomValues(array: Uint8Array): Uint8Array };
declare const console: { warn(...args: unknown[]): void };

export interface WriterOptions {
  /** Injectable so output is reproducible; the parity suite relies on it. */
  newGuid?: () => Uint8Array;
  now?: () => Date;
  onWarning?: (warning: ScheduleWarning) => void;
}

interface Proto {
  rec: Uint8Array;
  rec2: Uint8Array;
  meta: Uint8Array;
  meta2: Uint8Array;
  var: Array<[number, Uint8Array]>;
}
interface Row {
  rec: Uint8Array;
  rec2: Uint8Array;
  meta: Uint8Array;
  meta2: Uint8Array;
  var?: Array<[number, Uint8Array]>;
}

const dv = (b: Uint8Array) => new DataView(b.buffer, b.byteOffset, b.byteLength);
const copy = (b: Uint8Array) => new Uint8Array(b);
/** Little-endian scalars, the shapes var-data payloads come in. */
const u16 = (v: number) => { const b = new Uint8Array(2); dv(b).setUint16(0, v, true); return b; };
const i32 = (v: number) => { const b = new Uint8Array(4); dv(b).setInt32(0, v, true); return b; };
const f64 = (v: number) => { const b = new Uint8Array(8); dv(b).setFloat64(0, v, true); return b; };

function concat(parts: Uint8Array[]): Uint8Array {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

/** 7 (dayType, ranges) entries, Sunday first, for buildCalendarData. */
function dayBlocks(cal: Calendar): B.DayBlock[] {
  const out: B.DayBlock[] = [];
  for (let block = 0; block < 7; block++) {
    const wd = (block + 6) % 7; // block 0 = Sunday = weekday 6
    const val = cal.week?.[wd];
    if (val === undefined) out.push([B.CAL_DAY_DEFAULT, []]);
    else if (val === null) out.push([B.CAL_DAY_NONWORKING, []]);
    else out.push([B.CAL_DAY_WORKING, val]);
  }
  return out;
}

function exceptionTuples(cal: Calendar): B.CalendarExceptionTuple[] {
  return (cal.exceptions ?? [])
    .map((x) => [x.start, x.finish ?? x.start, x.name ?? ""] as B.CalendarExceptionTuple)
    .sort((a, b) => a[0].getTime() - b[0].getTime());
}

export class MppWriter {
  private root: Storage;
  private props!: Map<number, Uint8Array>;
  private propsHdr!: Uint8Array;
  private propsOrder!: number[];
  private templateStart!: Uint8Array;
  private newGuid: () => Uint8Array;
  private now: () => Date;
  private onWarning: (w: ScheduleWarning) => void;

  taskFm = new Map<number, B.FieldItem>();
  taskBit = new Map<number, number>();
  rscFm = new Map<number, B.FieldItem>();
  rscBit = new Map<number, number>();
  assnFm = new Map<number, B.FieldItem>();
  assnBit = new Map<number, number>();
  private rel2010Layout = false;

  private proto!: { summary: Proto; task: Proto };
  private stubs: Row[] = [];
  private taskMetaHdr!: Uint8Array;
  private taskMeta2Hdr!: Uint8Array;
  private taskVarHdr!: Uint8Array;
  private relProto: Proto | null = null;
  private relMetaHdr!: Uint8Array;
  private relMeta2Hdr!: Uint8Array;
  private assnProto: Proto | null = null;
  private assnMetaHdr!: Uint8Array;
  private assnMeta2Hdr!: Uint8Array;
  private assnVarHdr!: Uint8Array;
  private rscRows: Row[] = [];
  private rscProto: Row | null = null;
  private rscMetaHdr!: Uint8Array;
  private rscMeta2Hdr!: Uint8Array;
  private rscVarHdr!: Uint8Array;
  private calRows: Row[] = [];
  private calProto: Row | null = null;
  private calBaseRow: Row | null = null;
  private calCols: [number, number, number] | null = null;
  private calUidCol = 0;
  private calStandardUid: number | null = null;
  private calStandardGuid = new Uint8Array(16);
  private calMetaHdr!: Uint8Array;
  private calMeta2Hdr!: Uint8Array;
  private calVarHdr!: Uint8Array;
  private calVarEntries: B.VarValue[] = [];
  private calVarHi = B.RESOURCE_FIELD_HI;

  constructor(template: Uint8Array, opts: WriterOptions = {}) {
    let counter = 0;
    this.newGuid =
      opts.newGuid ??
      (() => {
        const b = new Uint8Array(16);
        crypto.getRandomValues(b);
        counter++;
        return b;
      });
    this.now =
      opts.now ??
      (() => {
        const d = new Date();
        return new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes()));
      });
    this.onWarning = opts.onWarning ?? ((w) => console.warn(w.message));
    this.root = readCfb(template);
    const props = B.parseProps(this.get(`${PRJ}/Props`));
    this.propsHdr = props.header;
    this.props = new Map(props.values);
    this.propsOrder = [...props.order];
    [this.taskFm, this.taskBit] = this.classMap(B.PROPS_TASK_FIELD_MAP);
    [this.rscFm, this.rscBit] = this.classMap(B.PROPS_RESOURCE_FIELD_MAP);
    [this.assnFm, this.assnBit] = this.classMap(B.PROPS_ASSIGNMENT_FIELD_MAP);
    this.templateStart = this.props.get(B.PROPS_PROJECT_START_DATE) ?? new Uint8Array(0);
    // the unmapped relation trailer moved between eras: 2010 files (field 9 at
    // offset 0) use type@12, lagUnits@14, lag@16; M365 uses type@12, lag@14,
    // lagUnits@18
    const [relFm] = this.classMap(B.PROPS_RELATION_FIELD_MAP);
    this.rel2010Layout = relFm.get(9)?.offset === 0;
    this.loadPrototypes();
  }

  // ---------------------------------------------------------- helpers ----

  private classMap(propsKey: number): [Map<number, B.FieldItem>, Map<number, number>] {
    const fm = new Map<number, B.FieldItem>();
    const bit = new Map<number, number>();
    const raw = this.props.get(propsKey);
    if (!raw) return [fm, bit];
    B.parseFieldMap(raw).forEach((it, i) => {
      if (it.inFixed && !fm.has(it.fieldId)) fm.set(it.fieldId, it);
      if (!bit.has(it.fieldId)) bit.set(it.fieldId, i);
    });
    return [fm, bit];
  }

  private get(path: string): Uint8Array {
    const data = this.root.get(path);
    if (!data) throw new Error(`template has no stream ${path}`);
    return data;
  }

  private has(path: string): boolean {
    return this.root.get(path) !== undefined;
  }

  private set(path: string, data: Uint8Array): void {
    this.root.set(path, data);
  }

  private meta(path: string, defaultSize: number): B.FixedMeta {
    return B.parseFixedMetaAuto(this.get(path), defaultSize);
  }

  /** Write a value into whichever block the class's field map puts it in. */
  private putf(
    fm: Map<number, B.FieldItem>,
    native: Record<string, number>,
    rec: Uint8Array,
    rec2: Uint8Array,
    name: string,
    kind: "u32" | "i32" | "u16" | "f64",
    value: number,
  ): void {
    const it = fm.get(native[name]!);
    if (!it) return;
    const dst = it.block === 0 ? rec : rec2;
    const size = kind === "f64" ? 8 : kind === "u16" ? 2 : 4;
    if (it.offset + size > dst.length) return;
    const d = dv(dst);
    if (kind === "u32") d.setUint32(it.offset, value >>> 0, true);
    else if (kind === "i32") d.setInt32(it.offset, value | 0, true);
    else if (kind === "u16") d.setUint16(it.offset, value & 0xffff, true);
    else d.setFloat64(it.offset, value, true);
  }

  private putfBytes(
    fm: Map<number, B.FieldItem>,
    native: Record<string, number>,
    rec: Uint8Array,
    rec2: Uint8Array,
    name: string,
    value: Uint8Array,
  ): void {
    const it = fm.get(native[name]!);
    if (!it) return;
    const dst = it.block === 0 ? rec : rec2;
    if (it.offset + value.length <= dst.length) dst.set(value, it.offset);
  }

  private putfTs(
    fm: Map<number, B.FieldItem>,
    native: Record<string, number>,
    rec: Uint8Array,
    rec2: Uint8Array,
    name: string,
    date: Date | null,
  ): void {
    this.putfBytes(fm, native, rec, rec2, name, B.encodeTimestamp(date));
  }

  private bitf(
    bits: Map<number, number>,
    native: Record<string, number>,
    meta: Uint8Array,
    meta2: Uint8Array,
    name: string,
    value: boolean,
  ): void {
    const idx = bits.get(native[name]!);
    if (idx !== undefined) B.setMetaBit(meta, meta2, idx, value);
  }

  private put(rec: Uint8Array, name: string, kind: "u32" | "i32" | "u16" | "f64", value: number): void {
    const it = this.taskFm.get(NATIVE[name]!);
    if (!it || it.block !== 0) return;
    const size = kind === "f64" ? 8 : kind === "u16" ? 2 : 4;
    if (it.offset + size > rec.length) return;
    const d = dv(rec);
    if (kind === "u32") d.setUint32(it.offset, value >>> 0, true);
    else if (kind === "i32") d.setInt32(it.offset, value | 0, true);
    else if (kind === "u16") d.setUint16(it.offset, value & 0xffff, true);
    else d.setFloat64(it.offset, value, true);
  }

  private putTs(rec: Uint8Array, name: string, date: Date | null): void {
    const it = this.taskFm.get(NATIVE[name]!);
    if (it && it.block === 0) rec.set(B.encodeTimestamp(date), it.offset);
  }

  private putBit(meta: Uint8Array, meta2: Uint8Array, name: string, value: boolean): void {
    const idx = this.taskBit.get(NATIVE[name]!);
    if (idx !== undefined) B.setMetaBit(meta, meta2, idx, value);
  }

  // ------------------------------------------------------ prototypes ----

  private loadPrototypes(): void {
    const t = `${PRJ}/TBkndTask`;
    const m = this.meta(`${t}/FixedMeta`, TASK_META_SIZE);
    const recs = B.splitFixedData(this.get(`${t}/FixedData`), m.items);
    const m2 = this.meta(`${t}/Fixed2Meta`, TASK_META2_SIZE);
    const recs2 = B.splitFixedData(this.get(`${t}/Fixed2Data`), m2.items);
    const vm = B.parseVarMeta(this.get(`${t}/VarMeta`));
    const vdata = this.get(`${t}/Var2Data`);
    this.taskMetaHdr = m.header;
    this.taskMeta2Hdr = m2.header;
    this.taskVarHdr = vm.header;

    // prototypes: the uid-0 project summary, and the first real leaf task —
    // in the recipe template Task 1 is itself a summary, so skip it
    const full = recs.map((r, i) => [i, r] as const).filter(([, r]) => r.length > 100).map(([i]) => i);
    if (!full.length) throw new Error("template has no task records to use as prototypes");
    const summaryI = full[0]!;
    const sumBit = this.taskBit.get(NATIVE["SUMMARY"]!);
    const taskI =
      full.slice(1).find((i) => sumBit === undefined || !B.metaBit(m.items[i]!, m2.items[i]!, sumBit)) ??
      (full.length > 1 ? full[full.length - 1]! : full[0]!);

    const protoFor = (i: number): Proto => {
      const uid = dv(recs[i]!).getUint32(0, true);
      const byType = vm.table.get(uid) ?? new Map<number, number>();
      const vars = [...byType.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([typ, off]) => [typ, B.readVar(vdata, off)] as [number, Uint8Array]);
      return { rec: recs[i]!, rec2: recs2[i]!, meta: m.items[i]!, meta2: m2.items[i]!, var: vars };
    };
    this.proto = { summary: protoFor(summaryI), task: protoFor(taskI) };
    // deleted/null-task stubs at the front of the block, kept verbatim
    this.stubs = recs
      .map((r, i) => ({ r, i }))
      .filter(({ r }) => r.length <= 16)
      .map(({ i }) => ({ rec: recs[i]!, rec2: recs2[i]!, meta: m.items[i]!, meta2: m2.items[i]! }));

    // relations
    const c = `${PRJ}/TBkndCons`;
    const rm = this.meta(`${c}/FixedMeta`, REL_META_SIZE);
    const rrecs = B.splitFixedData(this.get(`${c}/FixedData`), rm.items);
    const rm2 = this.meta(`${c}/Fixed2Meta`, REL_META2_SIZE);
    const rrecs2 = B.splitFixedData(this.get(`${c}/Fixed2Data`), rm2.items);
    this.relMetaHdr = rm.header;
    this.relMeta2Hdr = rm2.header;
    if (rrecs.length && rrecs[0]!.length >= 20) {
      this.relProto = { rec: rrecs[0]!, rec2: rrecs2[0]!, meta: rm.items[0]!, meta2: rm2.items[0]!, var: [] };
    }

    // assignments: the template's phantom per-task records are never emitted
    // as-is (they override task durations when joined by task uid), but the
    // first real one is the prototype
    const a = `${PRJ}/TBkndAssn`;
    const am = this.meta(`${a}/FixedMeta`, ASSN_META_SIZE);
    const arecs = B.splitFixedData(this.get(`${a}/FixedData`), am.items);
    const am2 = this.meta(`${a}/Fixed2Meta`, 53);
    const arecs2 = B.splitFixedData(this.get(`${a}/Fixed2Data`), am2.items);
    const avm = B.parseVarMeta(this.get(`${a}/VarMeta`));
    const avdata = this.get(`${a}/Var2Data`);
    this.assnMetaHdr = am.header.subarray(0, 16);
    this.assnMeta2Hdr = am2.header.subarray(0, 16);
    this.assnVarHdr = avm.header.subarray(0, 24);
    const taskIt = this.assnFm.get(ASSN_NATIVE["TASK_UNIQUE_ID"]!);
    for (let i = 0; i < arecs.length; i++) {
      const rec = arecs[i]!;
      if (rec.length > 50 && i < arecs2.length) {
        // skip the project-summary placeholder (task uid 0): it has no var
        // data, so it lacks the planned-work contour prototype
        if (taskIt && dv(rec).getUint32(taskIt.offset, true) === 0) continue;
        const uid = dv(rec).getUint32(0, true);
        const byType = avm.table.get(uid) ?? new Map<number, number>();
        this.assnProto = {
          rec,
          rec2: arecs2[i]!,
          meta: am.items[i]!,
          meta2: am2.items[i]!,
          var: [...byType.entries()].sort((x, y) => x[0] - y[0]).map(([typ, off]) => [typ, B.readVar(avdata, off)]),
        };
        break;
      }
    }

    // resources: the uid-0 "Unassigned" system record is the prototype
    const rs = `${PRJ}/TBkndRsc`;
    const rsm = this.meta(`${rs}/FixedMeta`, RSC_META_SIZE);
    const rsrecs = B.splitFixedData(this.get(`${rs}/FixedData`), rsm.items);
    const rsm2 = this.meta(`${rs}/Fixed2Meta`, 50);
    const rsrecs2 = B.splitFixedData(this.get(`${rs}/Fixed2Data`), rsm2.items);
    const rsvm = B.parseVarMeta(this.get(`${rs}/VarMeta`));
    const rsvdata = this.get(`${rs}/Var2Data`);
    this.rscMetaHdr = rsm.header.subarray(0, 16);
    this.rscMeta2Hdr = rsm2.header.subarray(0, 16);
    this.rscVarHdr = rsvm.header.subarray(0, 24);
    rsrecs.forEach((rec, i) => {
      const uid = rec.length >= 4 ? dv(rec).getUint32(0, true) : 0;
      const byType = rec.length > 16 ? (rsvm.table.get(uid) ?? new Map<number, number>()) : new Map<number, number>();
      const row: Row = {
        rec,
        rec2: rsrecs2[i] ?? new Uint8Array(0),
        meta: rsm.items[i]!,
        meta2: rsm2.items[i] ?? new Uint8Array(0),
        var: [...byType.entries()].sort((x, y) => x[0] - y[0]).map(([typ, off]) => [typ, B.readVar(rsvdata, off)]),
      };
      this.rscRows.push(row);
      if (rec.length > 100 && uid === 0) this.rscProto = row;
    });

    this.loadCalendars();
  }

  private loadCalendars(): void {
    // keep every existing record; clone the uid-0 resource's calendar for new
    // resources. The three dwords of a 12-byte calendar record are (calendar
    // uid, base calendar uid, resource uid) in an order that varies by Project
    // version, so detect the columns from the template's own records.
    const cal = `${PRJ}/TBkndCal`;
    if (!this.has(`${cal}/FixedData`)) return;
    const cm = this.meta(`${cal}/FixedMeta`, CAL_META_SIZE);
    const crecs = B.splitFixedData(this.get(`${cal}/FixedData`), cm.items);
    const cm2 = this.meta(`${cal}/Fixed2Meta`, 9);
    const crecs2 = B.splitFixedData(this.get(`${cal}/Fixed2Data`), cm2.items);
    const cvraw = this.get(`${cal}/VarMeta`);
    const cvm = B.parseVarMeta(cvraw);
    const cvdata = this.get(`${cal}/Var2Data`);
    this.calMetaHdr = cm.header.subarray(0, 16);
    this.calMeta2Hdr = cm2.header.subarray(0, 16);
    this.calVarHdr = cvm.header;
    this.calVarHi = cvraw.length >= 36 ? dv(cvraw).getUint16(34, true) : 0;
    this.calVarEntries = cvm.entries.map((e) => ({
      uid: e.uid,
      type: e.type,
      payload: B.readVar(cvdata, e.offset),
    }));
    this.calRows = crecs.map((rec, i) => ({
      rec,
      rec2: crecs2[i] ?? new Uint8Array(0),
      meta: cm.items[i]!,
      meta2: cm2.items[i] ?? new Uint8Array(0),
    }));

    // anchor on the uid-0 resource's calendar row: its three values — its own
    // calendar uid (from the resource record's CALENDAR_UID), the Standard
    // uid, and resource uid 0 — are distinct and identify each column
    let rsc0CalUid: number | null = null;
    if (this.rscProto) {
      const it = this.rscFm.get(RSC_NATIVE["CALENDAR_UID"]!);
      if (it) {
        const src = it.block === 0 ? this.rscProto.rec : this.rscProto.rec2;
        if (it.offset + 4 <= src.length) rsc0CalUid = dv(src).getInt32(it.offset, true);
      }
    }
    for (const row of this.calRows) {
      if (row.rec.length !== 12 || !rsc0CalUid) continue;
      const d = [0, 1, 2].map((j) => dv(row.rec).getInt32(j * 4, true));
      if (d.includes(rsc0CalUid) && d.includes(0)) {
        const uidCol = d.indexOf(rsc0CalUid);
        const rscCol = d.indexOf(0);
        const baseCol = [0, 1, 2].find((j) => j !== uidCol && j !== rscCol)!;
        this.calCols = [uidCol, baseCol, rscCol];
        this.calUidCol = uidCol;
        this.calStandardUid = d[baseCol]!;
        this.calProto = row;
        break;
      }
    }
    if (this.calCols) {
      for (const row of this.calRows) {
        if (row.rec.length === 12 && row !== this.calProto) {
          const d = [0, 1, 2].map((j) => dv(row.rec).getInt32(j * 4, true));
          if (d[this.calUidCol] === this.calStandardUid) {
            this.calBaseRow = row;
            this.calStandardGuid = copy(row.rec2.subarray(0, 16));
            break;
          }
        }
      }
    }
  }

  // ----------------------------------------------------------- build ----

  build(project: Project): Uint8Array {
    validate(project);
    const tasks = project.tasks;
    const relations = project.relations ?? [];
    const resources = project.resources ?? [];
    const assignments = project.assignments ?? [];
    const guidOf = new Map<number, Uint8Array>();
    for (const t of tasks) guidOf.set(t.uid, t.guid ?? this.newGuid());
    // the same rollup setBaseline() uses, so a baseline records exactly the
    // schedule the file describes
    const { byUid, children, deepestFirst, pattern, eff } = effectiveSchedule(project);

    for (const asn of assignments) {
      if (!byUid.has(asn.taskUid)) throw new Error(`assignment references unknown task uid ${asn.taskUid}`);
    }
    const directWork = new Map<number, number>();
    for (const asn of assignments) {
      const tenths = eff.get(asn.taskUid)!.tenths;
      directWork.set(
        asn.taskUid,
        (directWork.get(asn.taskUid) ?? 0) + tenths * WORK_SCALE * (asn.units ?? 1),
      );
    }
    const wsum = new Map<number, number>();
    for (const t of deepestFirst) {
      const kids = children.get(t.uid) ?? [];
      wsum.set(t.uid, (directWork.get(t.uid) ?? 0) + kids.reduce((s, k) => s + wsum.get(k.uid)!, 0));
    }

    // field validation and the percent-complete rollup (weighted by duration)
    const pctEff = new Map<number, number>();
    for (const t of deepestFirst) {
      if (t.constraint !== undefined && !(t.constraint in CONSTRAINT_TYPES)) {
        throw new Error(`task ${t.uid}: unknown constraint ${JSON.stringify(t.constraint)}`);
      }
      if (t.taskType !== undefined && !(t.taskType in TASK_TYPES)) {
        throw new Error(`task ${t.uid}: unknown taskType ${JSON.stringify(t.taskType)}`);
      }
      const pct = t.percentComplete ?? 0;
      if (pct < 0 || pct > 100) throw new Error(`task ${t.uid}: percentComplete out of range`);
      for (const [label, rec, limit] of [
        ["text", t.text, 30], ["number", t.number, 20], ["date", t.date, 10], ["flag", t.flag, 20],
      ] as const) {
        for (const n of Object.keys(rec ?? {}).map(Number)) {
          if (!(n >= 1 && n <= limit)) throw new Error(`task ${t.uid}: ${label}${n} out of range 1..${limit}`);
        }
      }
      const kids = children.get(t.uid) ?? [];
      if (kids.length) {
        const tot = kids.reduce((s, k) => s + eff.get(k.uid)!.tenths, 0);
        pctEff.set(
          t.uid,
          tot ? Math.round(kids.reduce((s, k) => s + eff.get(k.uid)!.tenths * pctEff.get(k.uid)!, 0) / tot) : 0,
        );
      } else {
        pctEff.set(t.uid, Math.trunc(pct));
      }
    }
    const topLevel = tasks.filter((t) => (t.parentUid ?? 0) === 0);
    const topTot = topLevel.reduce((s, t) => s + eff.get(t.uid)!.tenths, 0);
    const pct0 = topTot
      ? Math.round(topLevel.reduce((s, t) => s + eff.get(t.uid)!.tenths * pctEff.get(t.uid)!, 0) / topTot)
      : 0;

    // where each task's predecessors put its start, so only dates the links do
    // not already produce get pinned
    const linkStart = new Map<number, Date>();
    for (const rel of relations) {
      const pred = eff.get(rel.predUid);
      const succ = eff.get(rel.succUid);
      if (!pred || !succ) continue;
      const s = linkDrivenStart(rel, pred, succ.tenths, pattern);
      if (!s) continue;
      const prev = linkStart.get(rel.succUid);
      if (!prev || s.getTime() > prev.getTime()) linkStart.set(rel.succUid, s);
    }

    const warn = (message: string) => this.onWarning(new ScheduleWarning(message));
    const fmtDate = (d: Date) => d.toISOString().slice(0, 16).replace("T", " ");

    // Project cannot put a resource to work on a task whose own calendar shares
    // no working time with the resource's
    const namedCals = new Map((project.calendars ?? []).map((c) => [c.name ?? "Standard", c]));
    const assignedTasks = new Set(assignments.map((a) => a.taskUid));
    for (const t of tasks) {
      const cal = t.calendar ? namedCals.get(t.calendar) : undefined;
      if (cal && assignedTasks.has(t.uid) && !weeklyOverlapMinutes(workPattern(cal), pattern)) {
        warn(
          `task ${t.uid} ${JSON.stringify(t.name)} is on calendar ${JSON.stringify(cal.name ?? "")}, ` +
            `which shares no working time with the resource calendars; Project will schedule it ` +
            `ignoring the resource calendar`,
        );
      }
    }
    // a start on a window boundary is not working time: Project rolls it on
    for (const t of tasks) {
      if (t.manual || children.get(t.uid)?.length || eff.get(t.uid)!.tenths === 0) continue;
      const pat = t.calendar ? workPattern(namedCals.get(t.calendar) ?? null) : pattern;
      const rolled = nextWorkingMoment(eff.get(t.uid)!.start, pat);
      if (rolled.getTime() !== eff.get(t.uid)!.start.getTime()) {
        warn(
          `task ${t.uid} ${JSON.stringify(t.name)} starts ${fmtDate(eff.get(t.uid)!.start)}, which is ` +
            `not working time; Project will move it to ${fmtDate(rolled)}`,
        );
      }
    }
    // a declared start earlier than the links allow is one Project will move
    for (const [uid, implied] of linkStart) {
      const task = byUid.get(uid)!;
      if (task.manual || children.get(uid)?.length || task.calendar) continue;
      if (eff.get(uid)!.start.getTime() < implied.getTime()) {
        warn(
          `task ${uid} ${JSON.stringify(task.name)} starts ${fmtDate(eff.get(uid)!.start)} but its ` +
            `predecessors put it at ${fmtDate(implied)}; Project will move it`,
        );
      }
    }
    // Project reconciles a finished task against its assignments' timephased
    // actual work, which is not written yet
    for (const uid of new Set(assignments.map((a) => a.taskUid))) {
      if ((byUid.get(uid)?.percentComplete ?? 0) === 100) {
        warn(
          `task ${uid} ${JSON.stringify(byUid.get(uid)!.name)} is 100% complete and has assignments; ` +
            `Project recalculates progress from timephased actual work, which is not written yet, ` +
            `and will show it at 99%`,
        );
      }
    }

    const pStart = new Date(Math.min(...tasks.map((t) => eff.get(t.uid)!.start.getTime()), project.start.getTime()));
    const pFinish = new Date(Math.max(...tasks.map((t) => eff.get(t.uid)!.finish.getTime()), project.start.getTime()));
    const summaryGuid = this.newGuid();

    // ------------------------------------------------------ calendars ---
    const stdUid = this.calStandardUid ?? 1;
    let existingCalUids = [stdUid];
    if (this.calCols) {
      existingCalUids = this.calRows
        .filter((r) => r.rec.length === 12)
        .map((r) => dv(r.rec).getInt32(this.calCols![0] * 4, true));
    }
    let nextCalUid = Math.max(...existingCalUids) + 1;
    const calRowsOut: Row[] = [...this.calRows];
    let calMetaPatched = false;
    const calVarNew: B.VarValue[] = [];
    const namedCalUid = new Map<string, number>([["Standard", stdUid]]);
    if (project.calendar && (Object.keys(project.calendar.week ?? {}).length || project.calendar.exceptions?.length)) {
      calVarNew.push({
        uid: stdUid,
        type: CAL_DATA_VAR,
        payload: B.buildCalendarData(dayBlocks(project.calendar), exceptionTuples(project.calendar)),
      });
      // the record's meta gates the var data: byte 2 counts the record's var
      // entries, and the has-data flag marks the blob
      for (let i = 0; i < calRowsOut.length; i++) {
        if (this.calBaseRow && calRowsOut[i]!.rec === this.calBaseRow.rec) {
          const m = copy(calRowsOut[i]!.meta);
          m[2]! += 1;
          m[8]! |= 0xc0; // 0x80 in 2010-era metas, 0x40 in M365
          calRowsOut[i] = { ...calRowsOut[i]!, meta: m };
          calMetaPatched = true;
        }
      }
    }
    for (const cal of project.calendars ?? []) {
      if (!this.calBaseRow) throw new Error("template has no base calendar record to clone");
      const name = cal.name ?? "Standard";
      if (namedCalUid.has(name)) throw new Error(`duplicate calendar name ${JSON.stringify(name)}`);
      const uid = nextCalUid++;
      namedCalUid.set(name, uid);
      const crec = new Uint8Array(12);
      const cd = dv(crec);
      for (let j = 0; j < 3; j++) cd.setInt32(j * 4, j === this.calUidCol ? uid : -1, true);
      const crec2 = new Uint8Array(48);
      crec2.set(cal.guid ?? this.newGuid(), 0);
      const hasData = Boolean(Object.keys(cal.week ?? {}).length || cal.exceptions?.length);
      const m = copy(this.calBaseRow.meta);
      m[2] = hasData ? 2 : 1; // var entry count: name (+ data blob)
      if (hasData) m[8]! |= 0xc0;
      calRowsOut.push({ rec: crec, rec2: crec2, meta: m, meta2: copy(this.calBaseRow.meta2) });
      calVarNew.push({ uid, type: CAL_NAME_VAR, payload: B.encodeUnicode(name) });
      if (hasData) {
        calVarNew.push({ uid, type: CAL_DATA_VAR, payload: B.buildCalendarData(dayBlocks(cal), exceptionTuples(cal)) });
      }
    }

    // ---------------------------------------------------------- tasks ---
    const fixed: Uint8Array[] = [];
    const fixed2: Uint8Array[] = [];
    const metas: Uint8Array[] = [];
    const metas2: Uint8Array[] = [];
    const varEntries: B.VarValue[] = [];
    for (const s of this.stubs) {
      fixed.push(s.rec);
      fixed2.push(s.rec2);
      metas.push(copy(s.meta));
      metas2.push(copy(s.meta2));
    }

    const emit = (
      proto: Proto,
      uid: number,
      tid: number,
      name: string,
      start: Date,
      finish: Date,
      durTenths: number,
      level: number,
      parentUid: number,
      guid: Uint8Array,
      parentGuid: Uint8Array,
      isSummary: boolean,
      position: number,
      units = "d",
      estimated = false,
      work = 0,
      calUid: number | null = null,
      task: Task | null = null,
      pct = 0,
      baselines: Record<number, Baseline> = {},
    ): void => {
      const rec = copy(proto.rec);
      const rec2 = copy(proto.rec2);
      this.put(rec, "UNIQUE_ID", "u32", uid);
      this.put(rec, "ID", "u32", tid);
      this.put(rec, "OUTLINE_LEVEL", "u16", level);
      this.put(rec, "PARENT_UID", "u32", parentUid);
      this.put(rec, "DURATION", "i32", durTenths);
      this.put(rec, "REMAINING_DURATION", "i32", durTenths);
      this.put(rec, "WORK", "f64", work);
      this.put(rec, "REMAINING_WORK", "f64", work);
      const unitsWord = (isSummary ? SUMMARY_UNITS : UNITS_CODES[units]!) | (estimated ? ESTIMATED_FLAG : 0);
      this.put(rec, "ACTUAL_DURATION_UNITS", "u16", unitsWord);
      for (const f of ["START", "EARLY_START", "LATE_START"]) this.putTs(rec, f, start);
      for (const f of ["FINISH", "EARLY_FINISH", "LATE_FINISH"]) this.putTs(rec, f, finish);
      this.putTs(rec, "CREATED", this.now());
      // progress marks, or the template's own would be cloned onto every row
      if (isSummary) {
        this.putTs(rec, "SUMMARY_PROGRESS", null);
        this.putfTs(this.taskFm, NATIVE, rec, rec2, "SUMMARY_PROGRESS_PRIOR", null);
      } else {
        const prior = previousWorkingMoment(start, pattern);
        const mark = prior && prior.getTime() > project.start.getTime() ? prior : project.start;
        if (this.taskFm.has(NATIVE["SUMMARY_PROGRESS_PRIOR"]!)) {
          this.putTs(rec, "SUMMARY_PROGRESS", start);
          this.putfTs(this.taskFm, NATIVE, rec, rec2, "SUMMARY_PROGRESS_PRIOR", mark);
        } else {
          this.putTs(rec, "SUMMARY_PROGRESS", mark);
        }
      }
      rec2.set(guid, 0); // task GUID: field map block 1, offset 0
      dv(rec2).setFloat64(16, position, true);
      rec2.set(parentGuid, 24);

      const m = copy(proto.meta);
      const m2 = copy(proto.meta2);
      this.putBit(m, m2, "SUMMARY", isSummary);
      this.putBit(m, m2, "MILESTONE", !isSummary && durTenths === 0);
      this.putBit(m, m2, "ESTIMATED", estimated);
      const manual = task?.manual ?? false;
      this.putBit(m, m2, "MANUALLY_SCHEDULED", manual);
      if (manual) {
        this.putfTs(this.taskFm, NATIVE, rec, rec2, "MANUAL_START", start);
        this.putfTs(this.taskFm, NATIVE, rec, rec2, "MANUAL_FINISH", finish);
        this.putf(this.taskFm, NATIVE, rec, rec2, "MANUAL_DURATION", "i32", durTenths);
        this.putf(this.taskFm, NATIVE, rec, rec2, "MANUAL_DURATION_UNITS", "u16", UNITS_CODES[units]!);
      } else {
        this.putfTs(this.taskFm, NATIVE, rec, rec2, "MANUAL_START", null);
        this.putfTs(this.taskFm, NATIVE, rec, rec2, "MANUAL_FINISH", null);
        this.putf(this.taskFm, NATIVE, rec, rec2, "MANUAL_DURATION", "i32", -1);
      }
      if (calUid !== null) {
        this.put(rec, "CALENDAR_UNIQUE_ID", "i32", calUid);
        this.putBit(m, m2, "CALENDAR_UNIQUE_ID", true);
      }
      if (task) {
        let constraint = task.constraint;
        let cdate = task.constraintDate ?? null;
        const implied = linkStart.get(uid);
        if (
          constraint === undefined &&
          !manual &&
          !isSummary &&
          start.getTime() > project.start.getTime() &&
          implied?.getTime() !== start.getTime()
        ) {
          // hold the declared start the way Project pins a typed-in date;
          // tasks their predecessors already place stay ASAP
          constraint = "SNET";
          cdate = start;
        }
        if (constraint !== undefined) {
          this.put(rec, "CONSTRAINT_TYPE", "u16", CONSTRAINT_TYPES[constraint]!);
          this.putTs(rec, "CONSTRAINT_DATE", cdate);
          this.putBit(m, m2, "CONSTRAINT_TYPE", true);
        }
        if (task.deadline) {
          this.putTs(rec, "DEADLINE", task.deadline);
          this.putBit(m, m2, "DEADLINE", true);
        }
        this.put(rec, "PRIORITY", "u16", task.priority ?? 500);
        this.put(rec, "TYPE", "u16", TASK_TYPES[task.taskType ?? "fixed_units"]!);
        this.putBit(m, m2, "EFFORT_DRIVEN", task.effortDriven ?? false);
      }
      if (pct) {
        const actdur = Math.round((durTenths * pct) / 100);
        this.put(rec, "PERCENT_COMPLETE", "u16", pct);
        this.put(rec, "PERCENT_WORK_COMPLETE", "u16", pct);
        this.put(rec, "ACTUAL_DURATION", "i32", actdur);
        this.put(rec, "REMAINING_DURATION", "i32", durTenths - actdur);
        this.put(rec, "ACTUAL_WORK", "f64", (work * pct) / 100);
        this.put(rec, "REMAINING_WORK", "f64", (work * (100 - pct)) / 100);
        this.putTs(rec, "ACTUAL_START", start);
        const point = pct === 100 ? finish : advanceWorking(start, actdur, pattern);
        if (pct === 100) this.putTs(rec, "ACTUAL_FINISH", finish);
        this.putTs(rec, "STOP", point);
        this.putTs(rec, "RESUME", point);
        for (const f of ["PERCENT_COMPLETE", "ACTUAL_START", "ACTUAL_DURATION"]) this.putBit(m, m2, f, true);
      }

      const extraVars: Array<[number, Uint8Array]> = [];
      if (task) {
        if (task.notes) extraVars.push([NATIVE["NOTES"]!, encodeRtfNotes(task.notes)]);
        if (task.wbs !== undefined) extraVars.push([NATIVE["WBS"]!, B.encodeUnicode(task.wbs)]);
        for (const n of Object.keys(task.text ?? {}).map(Number).sort((a, b) => a - b)) {
          extraVars.push([TEXT_IDS[n - 1]!, B.encodeUnicode(task.text![n]!)]);
        }
        for (const n of Object.keys(task.number ?? {}).map(Number).sort((a, b) => a - b)) {
          const buf = new Uint8Array(8);
          dv(buf).setFloat64(0, task.number![n]!, true);
          extraVars.push([NUMBER_IDS[n - 1]!, buf]);
        }
        for (const n of Object.keys(task.date ?? {}).map(Number).sort((a, b) => a - b)) {
          extraVars.push([DATE_IDS[n - 1]!, B.encodeTimestamp(task.date![n]!)]);
        }
        for (const n of Object.keys(task.flag ?? {}).map(Number).sort((a, b) => a - b)) {
          const fbit = this.taskBit.get(FLAG_IDS[n - 1]!);
          if (fbit !== undefined) B.setMetaBit(m, m2, fbit, Boolean(task.flag![n]));
        }
      }
      // baselines are var data, not fixed fields — the fixed baseline fields
      // stay empty in files Project writes
      for (const slot of Object.keys(baselines).map(Number).sort((a, b) => a - b)) {
        const b = baselines[slot]!;
        const ids = TASK_BASELINE_IDS[slot]!;
        extraVars.push([ids.start, B.encodeTimestamp(b.start ?? null)]);
        extraVars.push([ids.finish, B.encodeTimestamp(b.finish ?? null)]);
        extraVars.push([ids.duration, i32(Math.round((b.durationDays ?? 0) * TENTHS_PER_DAY))]);
        extraVars.push([ids.units, u16(isSummary ? SUMMARY_UNITS : UNITS_CODES[units]!)]);
        extraVars.push([ids.work, f64((b.workHours ?? 0) * 600 * WORK_SCALE)]);
        extraVars.push([ids.cost, f64(b.cost ?? 0)]);
        const ex = TASK_BASELINE_EXTRAS[slot]; // only the evidenced slots
        if (ex) {
          extraVars.push([ex.deliverableStart, B.encodeTimestamp(null)]);
          extraVars.push([ex.deliverableFinish, B.encodeTimestamp(null)]);
          extraVars.push([ex.budgetWork, BASELINE_UNSET_DOUBLE]);
          extraVars.push([ex.budgetCost, BASELINE_UNSET_DOUBLE]);
        }
      }
      for (const [typ] of extraVars) {
        const fbit = this.taskBit.get(typ);
        if (fbit !== undefined) B.setMetaBit(m, m2, fbit, true);
      }
      m[2]! += extraVars.length; // meta byte 2 counts the record's var entries

      fixed.push(rec);
      fixed2.push(rec2);
      metas.push(m);
      metas2.push(m2);
      for (const [typ, payload] of proto.var) {
        varEntries.push({ uid, type: typ, payload: typ === NATIVE["NAME"] ? B.encodeUnicode(name) : payload });
      }
      for (const [typ, payload] of extraVars) varEntries.push({ uid, type: typ, payload });
    };

    // the project summary row spans every task's baseline, with work rolled up
    // from the top-level rows only (their children are already counted in)
    const summaryBaselines: Record<number, Baseline> = {};
    for (const slot of baselineSlots(project)) {
      const rows = tasks.map((t) => t.baselines?.[slot]).filter((b) => b !== undefined);
      const starts = rows.map((b) => b!.start).filter((d) => d !== undefined);
      const finishes = rows.map((b) => b!.finish).filter((d) => d !== undefined);
      if (!starts.length || !finishes.length) continue;
      const s0 = new Date(Math.min(...starts.map((d) => d!.getTime())));
      const f0 = new Date(Math.max(...finishes.map((d) => d!.getTime())));
      summaryBaselines[slot] = {
        start: s0,
        finish: f0,
        durationDays: Math.round((workingTenths(s0, f0, pattern) / TENTHS_PER_DAY) * 10_000) / 10_000,
        workHours: topLevel.reduce((sum, t) => sum + (t.baselines?.[slot]?.workHours ?? 0), 0),
      };
    }
    emit(
      this.proto.summary, 0, 0, project.title, pStart, pFinish,
      workingTenths(pStart, pFinish, pattern), 0, 0, summaryGuid, new Uint8Array(16), true, 1,
      "d", false, topLevel.reduce((s, t) => s + wsum.get(t.uid)!, 0), null, null, pct0,
      summaryBaselines,
    );
    let pos = 2;
    tasks.forEach((t, i) => {
      const parentGuid = (t.parentUid ?? 0) === 0 ? summaryGuid : guidOf.get(t.parentUid!)!;
      const e = eff.get(t.uid)!;
      let taskCal: number | null = null;
      if (t.calendar !== undefined) {
        if (!namedCalUid.has(t.calendar)) {
          throw new Error(`task ${t.uid} references unknown calendar ${JSON.stringify(t.calendar)}`);
        }
        taskCal = namedCalUid.get(t.calendar)!;
      }
      emit(
        this.proto.task, t.uid, i + 1, t.name, e.start, e.finish, e.tenths,
        t.outlineLevel ?? 1, t.parentUid ?? 0, guidOf.get(t.uid)!, parentGuid,
        Boolean(children.get(t.uid)?.length), pos, t.durationUnits ?? "d", t.estimated ?? false,
        wsum.get(t.uid)!, taskCal, t, pctEff.get(t.uid)!, t.baselines ?? {},
      );
      pos += 1;
    });

    /** FixedMeta's offset field (bytes 4..8) is the record's offset in FixedData. */
    const assemble = (recs: Uint8Array[], metaItems: Uint8Array[]): [Uint8Array, Uint8Array[]] => {
      let off = 0;
      const out: Uint8Array[] = [];
      recs.forEach((r, i) => {
        dv(metaItems[i]!).setUint32(4, off, true);
        out.push(r);
        off += r.length;
      });
      return [concat(out), metaItems];
    };

    let [fd, fm] = assemble(fixed, metas);
    let [fd2, fm2] = assemble(fixed2, metas2);
    const t = `${PRJ}/TBkndTask`;
    this.set(`${t}/FixedData`, fd);
    this.set(`${t}/FixedMeta`, B.buildFixedMeta(this.taskMetaHdr, fm, fd.length));
    this.set(`${t}/Fixed2Data`, fd2);
    this.set(`${t}/Fixed2Meta`, B.buildFixedMeta(this.taskMeta2Hdr, fm2, fd2.length));
    const taskVars = B.buildVarBlocks(this.taskVarHdr, varEntries);
    this.set(`${t}/VarMeta`, taskVars.meta);
    this.set(`${t}/Var2Data`, taskVars.data);

    // ------------------------------------------------------ relations ---
    if (relations.length && !this.relProto) {
      throw new Error(
        "template has no dependency records to use as a prototype; save the template with at " +
          "least one linked pair of tasks",
      );
    }
    const rfixed: Uint8Array[] = [];
    const rfixed2: Uint8Array[] = [];
    const rmeta: Uint8Array[] = [];
    const rmeta2: Uint8Array[] = [];
    relations.forEach((r, i) => {
      const rec = copy(this.relProto!.rec);
      const rec2 = copy(this.relProto!.rec2);
      const rd = dv(rec);
      rd.setUint32(0, i + 1, true);
      rd.setUint32(4, r.predUid, true);
      rd.setUint32(8, r.succUid, true);
      rd.setUint16(12, REL_TYPES[r.type ?? "FS"]!, true);
      const lag = Math.round((r.lagDays ?? 0) * TENTHS_PER_DAY);
      if (this.rel2010Layout) {
        rd.setUint16(14, 7, true); // lag units (days), then lag
        rd.setInt32(16, lag, true);
      } else {
        rd.setInt32(14, lag, true); // lag, then lag units
        rd.setUint16(18, 7, true);
      }
      rec2.set(this.newGuid(), 0);
      rec2.set(guidOf.get(r.predUid)!, 16);
      rec2.set(guidOf.get(r.succUid)!, 32);
      rfixed.push(rec);
      rfixed2.push(rec2);
      rmeta.push(copy(this.relProto!.meta));
      rmeta2.push(copy(this.relProto!.meta2));
    });
    const [rfd, rfm] = assemble(rfixed, rmeta);
    const [rfd2, rfm2] = assemble(rfixed2, rmeta2);
    const c = `${PRJ}/TBkndCons`;
    this.set(`${c}/FixedData`, rfd);
    this.set(`${c}/FixedMeta`, B.buildFixedMeta(this.relMetaHdr, rfm, rfd.length));
    this.set(`${c}/Fixed2Data`, rfd2);
    this.set(`${c}/Fixed2Meta`, B.buildFixedMeta(this.relMeta2Hdr, rfm2, rfd2.length));

    // ------------------------------------------------------ resources ---
    let rscCount: number | null = null;
    if (resources.length) {
      if (!this.rscProto) throw new Error("template has no uid-0 resource record to use as a prototype");
      const uids = resources.map((r) => r.uid);
      if (new Set(uids).size !== uids.length || uids.some((u) => u <= 0)) {
        throw new Error("resource uids must be unique and > 0");
      }
      const rrows: Row[] = [...this.rscRows];
      const rvarEntries: B.VarValue[] = [];
      for (const row of this.rscRows) {
        if (row.rec.length > 16) {
          const uid = dv(row.rec).getUint32(0, true);
          for (const [typ, payload] of row.var ?? []) rvarEntries.push({ uid, type: typ, payload });
        }
      }
      resources.forEach((res, idx0) => {
        const idx = idx0 + 1;
        const rec = copy(this.rscProto!.rec);
        const rec2 = copy(this.rscProto!.rec2);
        const m = copy(this.rscProto!.meta);
        const m2 = copy(this.rscProto!.meta2);
        const guid = res.guid ?? this.newGuid();
        // a resource baseline is work and cost only — Project stores no dates
        const rextra: Array<[number, Uint8Array]> = [];
        for (const slot of Object.keys(res.baselines ?? {}).map(Number).sort((a, b) => a - b)) {
          const b = res.baselines![slot]!;
          const ids = RSC_BASELINE_IDS[slot]!;
          rextra.push([ids.work, f64((b.workHours ?? 0) * 600 * WORK_SCALE)]);
          rextra.push([ids.cost, f64(b.cost ?? 0)]);
          const bg = RSC_BASELINE_BUDGET[slot]; // unset, as Project writes them
          if (bg) {
            rextra.push([bg.work, BASELINE_UNSET_DOUBLE]);
            rextra.push([bg.cost, BASELINE_UNSET_DOUBLE]);
          }
        }
        m[2] = (this.rscProto!.var ?? []).length + 1 + (res.initials ? 1 : 0)
          + (res.email ? 1 : 0) + rextra.length;
        const calUid = nextCalUid++;
        this.putf(this.rscFm, RSC_NATIVE, rec, rec2, "UNIQUE_ID", "u32", res.uid);
        this.putf(this.rscFm, RSC_NATIVE, rec, rec2, "ID", "u32", idx);
        this.putf(this.rscFm, RSC_NATIVE, rec, rec2, "MAX_UNITS", "f64", (res.maxUnits ?? 1) * PCT_SCALE);
        this.putf(this.rscFm, RSC_NATIVE, rec, rec2, "CALENDAR_UID", "i32", calUid);
        this.putf(this.rscFm, RSC_NATIVE, rec, rec2, "POSITION", "f64", idx + 1);
        this.putfBytes(this.rscFm, RSC_NATIVE, rec, rec2, "GUID", guid);
        this.putfBytes(this.rscFm, RSC_NATIVE, rec, rec2, "CALENDAR_GUID", guid);
        for (const name of ["UNIQUE_ID", "ID", "NAME", "MAX_UNITS"]) {
          this.bitf(this.rscBit, RSC_NATIVE, m, m2, name, true);
        }
        this.bitf(this.rscBit, RSC_NATIVE, m, m2, "INITIALS", Boolean(res.initials));
        this.bitf(this.rscBit, RSC_NATIVE, m, m2, "EMAIL_ADDRESS", Boolean(res.email));
        rrows.push({ rec, rec2, meta: m, meta2: m2 });
        for (const [typ, payload] of this.rscProto!.var ?? []) {
          rvarEntries.push({ uid: res.uid, type: typ, payload });
        }
        rvarEntries.push({ uid: res.uid, type: RSC_NATIVE["NAME"]!, payload: B.encodeUnicode(res.name) });
        if (res.initials) {
          rvarEntries.push({ uid: res.uid, type: RSC_NATIVE["INITIALS"]!, payload: B.encodeUnicode(res.initials) });
        }
        if (res.email) {
          rvarEntries.push({ uid: res.uid, type: RSC_NATIVE["EMAIL_ADDRESS"]!, payload: B.encodeUnicode(res.email) });
        }
        for (const [typ, payload] of rextra) {
          const fbit = this.rscBit.get(typ);
          if (fbit !== undefined) B.setMetaBit(m, m2, fbit, true);
          rvarEntries.push({ uid: res.uid, type: typ, payload });
        }
        // per-resource calendar: (uid, base = Standard, resource uid)
        if (this.calProto && this.calCols) {
          const [uc, bc, rc] = this.calCols;
          const crec = copy(this.calProto.rec);
          const cd = dv(crec);
          cd.setInt32(uc * 4, calUid, true);
          cd.setInt32(bc * 4, this.calStandardUid ?? 1, true);
          cd.setInt32(rc * 4, res.uid, true);
          const crec2 = copy(this.calProto.rec2);
          crec2.set(guid, 0);
          crec2.set(guid, 16);
          crec2.set(this.calStandardGuid, 32);
          calRowsOut.push({
            rec: crec,
            rec2: crec2,
            meta: copy(this.calProto.meta),
            meta2: copy(this.calProto.meta2),
          });
        }
      });
      const [rsfd, rsfm] = assemble(rrows.map((r) => r.rec), rrows.map((r) => copy(r.meta)));
      const [rsfd2, rsfm2] = assemble(rrows.map((r) => r.rec2), rrows.map((r) => copy(r.meta2)));
      this.set(`${PRJ}/TBkndRsc/FixedData`, rsfd);
      this.set(`${PRJ}/TBkndRsc/FixedMeta`, B.buildFixedMeta(this.rscMetaHdr, rsfm, rsfd.length));
      this.set(`${PRJ}/TBkndRsc/Fixed2Data`, rsfd2);
      this.set(`${PRJ}/TBkndRsc/Fixed2Meta`, B.buildFixedMeta(this.rscMeta2Hdr, rsfm2, rsfd2.length));
      const rv = B.buildVarBlocks(this.rscVarHdr, rvarEntries, B.RESOURCE_FIELD_HI);
      this.set(`${PRJ}/TBkndRsc/VarMeta`, rv.meta);
      this.set(`${PRJ}/TBkndRsc/Var2Data`, rv.data);
      rscCount = rrows.length;
    }

    // TBkndCal: rewrite the fixed streams when rows were added or metas
    // patched, the var streams when names or data blobs were added
    if (calRowsOut.length !== this.calRows.length || calMetaPatched) {
      const [cfd, cfm] = assemble(calRowsOut.map((r) => r.rec), calRowsOut.map((r) => copy(r.meta)));
      const [cfd2, cfm2] = assemble(calRowsOut.map((r) => r.rec2), calRowsOut.map((r) => copy(r.meta2)));
      this.set(`${PRJ}/TBkndCal/FixedData`, cfd);
      this.set(`${PRJ}/TBkndCal/FixedMeta`, B.buildFixedMeta(this.calMetaHdr, cfm, cfd.length));
      this.set(`${PRJ}/TBkndCal/Fixed2Data`, cfd2);
      this.set(`${PRJ}/TBkndCal/Fixed2Meta`, B.buildFixedMeta(this.calMeta2Hdr, cfm2, cfd2.length));
    }
    if (calVarNew.length) {
      const cv = B.buildVarBlocks(this.calVarHdr, [...this.calVarEntries, ...calVarNew], this.calVarHi);
      this.set(`${PRJ}/TBkndCal/VarMeta`, cv.meta);
      this.set(`${PRJ}/TBkndCal/Var2Data`, cv.data);
      // Project reads base-calendar blobs only when this count says edited base
      // calendars exist (resource-calendar blobs load regardless)
      const nEdited = calVarNew.filter((v) => v.type === CAL_DATA_VAR).length;
      if (this.props.has(B.PROPS_EDITED_BASE_CALENDARS)) {
        const buf = new Uint8Array(4);
        dv(buf).setUint32(0, nEdited, true);
        this.props.set(B.PROPS_EDITED_BASE_CALENDARS, buf);
      }
    }

    // ---------------------------------------------------- assignments ---
    const rscByUid = new Map(resources.map((r) => [r.uid, r]));
    const rscGuid = new Map<number, Uint8Array>();
    for (const res of resources) rscGuid.set(res.uid, res.guid ?? new Uint8Array(16));
    for (const asn of assignments) {
      if (!rscByUid.has(asn.resourceUid)) {
        throw new Error(`assignment references unknown resource uid ${asn.resourceUid}`);
      }
    }
    // Project keeps an assignment row for every leaf task, with a placeholder
    // resource where nobody is assigned; without them it opens on the
    // template's row count until the view is rebuilt
    const assignedSet = new Set(assignments.map((a) => a.taskUid));
    // a placeholder row has no Assignment to carry baselines, so it takes the
    // task's own — which is what Project writes on these rows
    type Spec = [number, number, number, Record<number, Baseline> | null];
    const specs: Spec[] = assignments.map((a) => [a.taskUid, a.resourceUid, a.units ?? 1, a.baselines ?? {}]);
    for (const t of tasks) {
      if (!assignedSet.has(t.uid) && !children.get(t.uid)?.length) {
        specs.push([t.uid, NULL_RESOURCE_UID, 1, null]);
      }
    }
    if (specs.length && !this.assnProto) {
      throw new Error("template has no assignment records to use as a prototype");
    }
    const afixed: Uint8Array[] = [];
    const afixed2: Uint8Array[] = [];
    const ameta: Uint8Array[] = [];
    const ameta2: Uint8Array[] = [];
    const avarEntries: B.VarValue[] = [];
    specs.forEach(([taskUid, resourceUid, units, specBaselines], i0) => {
      const i = i0 + 1;
      const empty = resourceUid === NULL_RESOURCE_UID;
      const task = byUid.get(taskUid)!;
      const e = eff.get(taskUid)!;
      const rec = copy(this.assnProto!.rec);
      const rec2 = copy(this.assnProto!.rec2);
      const m = copy(this.assnProto!.meta);
      const m2 = copy(this.assnProto!.meta2);
      const put = (name: string, kind: "u32" | "i32" | "f64", value: number) =>
        this.putf(this.assnFm, ASSN_NATIVE, rec, rec2, name, kind, value);
      put("UNIQUE_ID", "u32", i);
      put("TASK_UNIQUE_ID", "u32", taskUid);
      put("RESOURCE_UNIQUE_ID", "i32", resourceUid);
      put("UNITS", "f64", units * PCT_SCALE);
      // a placeholder row carries the task's own duration as work, exactly as a
      // real assignment does — only the resource differs
      const work = e.tenths * WORK_SCALE * units;
      for (const name of ["WORK", "REGULAR_WORK", "REMAINING_WORK"]) put(name, "f64", work);
      const tpct = pctEff.get(taskUid) ?? 0;
      let reached = e.start;
      if (tpct) {
        put("ACTUAL_WORK", "f64", (work * tpct) / 100);
        put("REMAINING_WORK", "f64", (work * (100 - tpct)) / 100);
        // how far work has got: Project reconciles the task's actuals against
        // this, and a stop still at the start knocked a finished task back to 99%
        reached = tpct === 100 ? e.finish : advanceWorking(e.start, Math.round((e.tenths * tpct) / 100), pattern);
      }
      this.putfTs(this.assnFm, ASSN_NATIVE, rec, rec2, "START", e.start);
      for (const name of ["RESUME", "STOP"]) this.putfTs(this.assnFm, ASSN_NATIVE, rec, rec2, name, reached);
      this.putfTs(this.assnFm, ASSN_NATIVE, rec, rec2, "FINISH", e.finish);
      this.putfBytes(this.assnFm, ASSN_NATIVE, rec, rec2, "GUID", this.newGuid());
      this.putfBytes(this.assnFm, ASSN_NATIVE, rec, rec2, "TASK_GUID", guidOf.get(taskUid)!);
      this.putfBytes(
        this.assnFm, ASSN_NATIVE, rec, rec2, "RESOURCE_GUID",
        empty ? NULL_RESOURCE_GUID : (rscGuid.get(resourceUid) ?? new Uint8Array(16)),
      );
      for (const name of ["UNIQUE_ID", "TASK_UNIQUE_ID", "RESOURCE_UNIQUE_ID", "UNITS", "WORK"]) {
        this.bitf(this.assnBit, ASSN_NATIVE, m, m2, name, true);
      }
      afixed.push(rec);
      afixed2.push(rec2);
      ameta.push(m);
      ameta2.push(m2);
      let nvars = 0;
      for (const [typ, payload0] of this.assnProto!.var) {
        let payload = payload0;
        if (typ === ASSN_NATIVE["CREATED"]) {
          payload = B.encodeTimestamp(this.now());
        } else if (typ === ASSN_NATIVE["PLANNED_WORK_DATA"] && payload0.length >= 36) {
          // the planned-work contour: Project schedules the assignment from
          // this blob, not from the fixed WORK field
          const b2 = copy(payload0);
          const bd = dv(b2);
          bd.setFloat64(8, units * PCT_SCALE * 16, true);
          bd.setFloat64(16, work, true);
          bd.setUint32(24, e.tenths * 8, true);
          payload = b2;
        }
        avarEntries.push({ uid: i, type: typ, payload });
        nvars += 1;
      }
      for (const typ of ASSN_VAR_EMPTY) {
        if (typ === 667 && !empty) continue;
        avarEntries.push({ uid: i, type: typ, payload: new Uint8Array(16) });
        nvars += 1;
      }
      const abl = specBaselines ?? task.baselines ?? {};
      for (const slot of Object.keys(abl).map(Number).sort((x, y) => x - y)) {
        const b = abl[slot]!;
        const ids = ASSN_BASELINE_IDS[slot]!;
        const entries: Array<[number, Uint8Array]> = [
          [ids.start, B.encodeTimestamp(b.start ?? null)],
          [ids.finish, B.encodeTimestamp(b.finish ?? null)],
          [ids.work, f64((b.workHours ?? 0) * 600 * WORK_SCALE)],
          [ids.cost, f64(b.cost ?? 0)],
        ];
        const bg = ASSN_BASELINE_BUDGET[slot]; // unset, as Project writes them
        if (bg) entries.push([bg.work, BASELINE_UNSET_DOUBLE], [bg.cost, BASELINE_UNSET_DOUBLE]);
        for (const [typ, payload] of entries) {
          const fbit = this.assnBit.get(typ);
          if (fbit !== undefined) B.setMetaBit(m, m2, fbit, true);
          avarEntries.push({ uid: i, type: typ, payload });
          nvars += 1;
        }
      }
      m[2] = nvars; // meta byte 2 counts the record's var entries
      void task;
    });
    const a = `${PRJ}/TBkndAssn`;
    const [afd, afm] = assemble(afixed, ameta);
    const [afd2, afm2] = assemble(afixed2, ameta2);
    this.set(`${a}/FixedData`, afd);
    this.set(`${a}/FixedMeta`, B.buildFixedMeta(this.assnMetaHdr, afm, afd.length));
    this.set(`${a}/Fixed2Data`, afd2);
    this.set(`${a}/Fixed2Meta`, B.buildFixedMeta(this.assnMeta2Hdr, afm2, afd2.length));
    const av = B.buildVarBlocks(this.assnVarHdr, avarEntries, B.ASSIGNMENT_FIELD_HI);
    this.set(`${a}/VarMeta`, av.meta);
    this.set(`${a}/Var2Data`, av.data);

    // ---------------------------------------------------------- props ---
    const u32 = (n: number) => {
      const b = new Uint8Array(4);
      dv(b).setUint32(0, n >>> 0, true);
      return b;
    };
    const counters: Array<[number, number]> = [
      [B.PROPS_TASK_RECORD_COUNT, fixed.length],
      [B.PROPS_ASSN_RECORD_COUNT, afixed.length], // includes the placeholder rows
      [B.PROPS_REL_RECORD_COUNT, relations.length],
    ];
    if (rscCount !== null) {
      counters.push([B.PROPS_RESOURCE_RECORD_COUNT, rscCount]);
      counters.push([B.PROPS_RESOURCE_RECORD_COUNT + 1, rscCount + 2]); // 0x1000003
    }
    for (const [key, n] of counters) if (this.props.has(key)) this.props.set(key, u32(n));

    // Project truncates its var-data read at the declared size, so a stale
    // value hides names and calendar data
    for (const [storage, key] of Object.entries(B.PROPS_VAR2DATA_SIZE)) {
      if (this.props.has(key)) {
        const data = this.root.get(`${PRJ}/${storage}/Var2Data`);
        if (data) this.props.set(key, u32(data.length));
      }
    }

    if (project.defaultCalendar !== undefined) {
      if (!namedCalUid.has(project.defaultCalendar)) {
        throw new Error(`unknown default calendar ${JSON.stringify(project.defaultCalendar)}`);
      }
      if (this.props.has(B.PROPS_DEFAULT_CALENDAR_NAME)) {
        this.props.set(
          B.PROPS_DEFAULT_CALENDAR_NAME,
          concat([B.encodeUtf16le(project.defaultCalendar), new Uint8Array(4)]),
        );
      }
    }
    this.props.set(B.PROPS_PROJECT_START_DATE, B.encodeTimestamp(project.start));
    if (this.props.has(B.PROPS_PROJECT_FINISH_DATE)) {
      this.props.set(B.PROPS_PROJECT_FINISH_DATE, B.encodeTimestamp(pFinish));
    }
    if (project.statusDate && this.props.has(B.PROPS_STATUS_DATE)) {
      this.props.set(B.PROPS_STATUS_DATE, B.encodeTimestamp(project.statusDate));
    }
    if (project.currencySymbol !== undefined && this.props.has(B.PROPS_CURRENCY_SYMBOL)) {
      this.props.set(B.PROPS_CURRENCY_SYMBOL, B.encodeUnicode(project.currencySymbol));
    }
    if (project.currencyCode !== undefined && this.props.has(B.PROPS_CURRENCY_CODE)) {
      this.props.set(B.PROPS_CURRENCY_CODE, B.encodeUnicode(project.currencyCode));
    }
    // when the baseline was saved; NA when there is none, as Project leaves it
    // after Clear Baseline
    if (this.props.has(PROPS_BASELINE_SAVED)) {
      const hasBaseline = tasks.some((t) => Object.keys(t.baselines ?? {}).length > 0);
      this.props.set(PROPS_BASELINE_SAVED, B.encodeTimestamp(hasBaseline ? this.now() : null));
    }
    // stale 2010-era next-uid counters make Project renumber task uids
    if (this.props.has(B.PROPS_LEGACY_NEXT_UIDS)) {
      this.props.delete(B.PROPS_LEGACY_NEXT_UIDS);
      this.propsOrder = this.propsOrder.filter((k) => k !== B.PROPS_LEGACY_NEXT_UIDS);
    }
    if (this.props.has(B.PROPS_TITLE)) {
      this.props.set(B.PROPS_TITLE, concat([B.encodeUtf16le(project.title), new Uint8Array(4)]));
    }
    this.set(`${PRJ}/Props`, B.buildProps(this.propsHdr, this.props, this.propsOrder));

    // the Gantt scroll position: the view stores the visible date as the
    // template's project start, so retarget it to this schedule
    const newStart = B.encodeTimestamp(project.start);
    if (this.templateStart.length === 4 && !this.templateStart.every((b, i) => b === newStart[i])) {
      const vd = this.root.get("   214/CV_iew/Var2Data");
      if (vd) {
        const out = copy(vd);
        for (let i = 0; i + 4 <= out.length; i++) {
          if (out[i] === this.templateStart[0] && out[i + 1] === this.templateStart[1] &&
              out[i + 2] === this.templateStart[2] && out[i + 3] === this.templateStart[3]) {
            out.set(newStart, i);
          }
        }
        this.set("   214/CV_iew/Var2Data", out);
      }
    }

    // document metadata
    const si = this.root.get("SummaryInformation");
    if (si) {
      const siUpdates = new Map<number, string>([[2, project.title]]);
      for (const [pid, val] of [[3, project.subject], [4, project.author], [5, project.keywords],
                                [6, project.comments]] as Array<[number, string | undefined]>) {
        if (val !== undefined) siUpdates.set(pid, val);
      }
      this.set("SummaryInformation", B.updatePropertySetStrings(si, siUpdates));
      const dsiUpdates = new Map<number, string>();
      for (const [pid, val] of [[14, project.manager], [15, project.company],
                                [2, project.category]] as Array<[number, string | undefined]>) {
        if (val !== undefined) dsiUpdates.set(pid, val);
      }
      const dsi = this.root.get("DocumentSummaryInformation");
      if (dsiUpdates.size && dsi) {
        this.set("DocumentSummaryInformation", B.updatePropertySetStrings(dsi, dsiUpdates));
      }
    }

    return writeCfb(this.root, PROJECT_CLSID);
  }
}

/** Convenience: build a project against a template, both as bytes. */
export function writeProject(template: Uint8Array, project: Project, opts?: WriterOptions): Uint8Array {
  return new MppWriter(template, opts).build(project);
}
