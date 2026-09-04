/**
 * The project model, the native field tables, and the scheduling arithmetic
 * the writer needs — a port of the front half of `writer.py`.
 *
 * Dates are plain `Date` values read in **UTC**: the format stores wall-clock
 * times with no zone, so `Date.UTC(2027, 2, 1, 8)` means 08:00 in the plan.
 */

export const PRJ = "   114";
export const TASK_META_SIZE = 47;
export const TASK_META2_SIZE = 92;
export const REL_META_SIZE = 10;
export const REL_META2_SIZE = 9;
export const RSC_META_SIZE = 37;
export const ASSN_META_SIZE = 34;
export const CAL_META_SIZE = 10;
export const TENTHS_PER_DAY = 4800; // 8h * 60m * 10

export const PROJECT_CLSID = Uint8Array.from([
  0x3a, 0x8f, 0xb7, 0x74, 0xc8, 0xc8, 0xd1, 0x11, 0xbe, 0x11, 0x00, 0xc0, 0x4f, 0xb6, 0xfa, 0xf1,
]);

export const NATIVE: Record<string, number> = {
  UNIQUE_ID: 86, ID: 23, NAME: 14, START: 35, FINISH: 36, DURATION: 29,
  REMAINING_DURATION: 31, OUTLINE_LEVEL: 249, PARENT_UID: 160, EARLY_START: 37,
  EARLY_FINISH: 38, LATE_START: 39, LATE_FINISH: 40, CREATED: 93, GUID: 1143,
  MILESTONE: 24, SUMMARY: 92, ESTIMATED: 396, ACTUAL_DURATION_UNITS: 181,
  TASK_MODE: 1280, WORK: 0, REMAINING_WORK: 4, CALENDAR_UNIQUE_ID: 401,
  MANUAL_START: 1283, MANUAL_FINISH: 1284, MANUAL_DURATION: 1288,
  MANUAL_DURATION_UNITS: 1289,
  MANUALLY_SCHEDULED: 1408, // the flag M365 reads; 1280 stays set either way
  NOTES: 15, WBS: 16, CONSTRAINT_TYPE: 17, CONSTRAINT_DATE: 18, DEADLINE: 437,
  PERCENT_COMPLETE: 32, PERCENT_WORK_COMPLETE: 33, ACTUAL_START: 41,
  ACTUAL_FINISH: 42, ACTUAL_DURATION: 28, ACTUAL_WORK: 2, STOP: 100,
  RESUME: 99, PRIORITY: 25, TYPE: 128, EFFORT_DRIVEN: 132,
  SUMMARY_PROGRESS: 387, SUMMARY_PROGRESS_PRIOR: 1255,
};

/** Custom field ids, index 0 = Text1 / Number1 / Date1 / Flag1. */
export const TEXT_IDS = [51, 54, 57, 60, 63, 66, 67, 68, 69, 70, 317, 318, 319, 320, 321,
  322, 323, 324, 325, 326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 336];
export const NUMBER_IDS = [87, 88, 89, 90, 91, 302, 303, 304, 305, 306, 307, 308, 309, 310,
  311, 312, 313, 314, 315, 316];
export const DATE_IDS = [265, 266, 267, 268, 269, 270, 271, 272, 273, 274];
export const FLAG_IDS = [72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 292, 293, 294, 295, 296,
  297, 298, 299, 300, 301];

export const CONSTRAINT_TYPES: Record<string, number> = {
  ASAP: 0, ALAP: 1, MSO: 2, MFO: 3, SNET: 4, SNLT: 5, FNET: 6, FNLT: 7,
};
export const TASK_TYPES: Record<string, number> = {
  fixed_units: 0, fixed_duration: 1, fixed_work: 2,
};
export const CAL_NAME_VAR = 1;
export const CAL_DATA_VAR = 8;

export const RSC_NATIVE: Record<string, number> = {
  UNIQUE_ID: 27, ID: 0, NAME: 1, INITIALS: 2, EMAIL_ADDRESS: 35, MAX_UNITS: 4,
  CALENDAR_UID: 56, GUID: 728, CALENDAR_GUID: 729, POSITION: 730,
};
export const ASSN_NATIVE: Record<string, number> = {
  UNIQUE_ID: 0, TASK_UNIQUE_ID: 1, RESOURCE_UNIQUE_ID: 2, START: 20, FINISH: 21,
  RESUME: 24, STOP: 264, UNITS: 7, WORK: 8, ACTUAL_WORK: 10, REGULAR_WORK: 11,
  REMAINING_WORK: 12, GUID: 636, TASK_GUID: 637, RESOURCE_GUID: 638, CREATED: 634,
  PLANNED_WORK_DATA: 49, ACTUAL_WORK_DATA: 50,
};
export const REL_TYPES: Record<string, number> = { FF: 0, FS: 1, SF: 2, SS: 3 };

/** An assignment row for a task with nobody assigned. */
export const NULL_RESOURCE_UID = -65535;
export const NULL_RESOURCE_GUID = Uint8Array.from(
  "788bcba08c2a6d4300000000000000ff".match(/../g)!.map((h) => parseInt(h, 16)),
);
/** 16 zero bytes each; 667 only on the placeholder rows. */
export const ASSN_VAR_EMPTY = [665, 667];

export const PCT_SCALE = 10000.0; // 10000.0 = 100%
export const WORK_SCALE = 100.0; // work doubles are minutes*1000 = duration tenths * 100
export const UNITS_CODES: Record<string, number> = { m: 3, h: 5, d: 7, w: 9, mo: 11 };
export const SUMMARY_UNITS = 0x15; // the units word Project writes on summary rows
export const ESTIMATED_FLAG = 0x20; // OR'ed into the units word; shows as "3 days?"
/** The Standard calendar: Mon-Fri, 08:00-12:00 and 13:00-17:00. */
export const WORK_WINDOWS: Array<[number, number]> = [[8 * 60, 12 * 60], [13 * 60, 17 * 60]];

const DAY_MS = 86_400_000;

// ------------------------------------------------------------------ model --

export interface Task {
  uid: number;
  name: string;
  start: Date;
  finish: Date;
  /** Working days; 0 = milestone; ignored on summary rows (rolled up). */
  durationDays?: number;
  outlineLevel?: number;
  /** 0 = under the project summary. */
  parentUid?: number;
  /** Display units: m, h, d (default), w, mo. */
  durationUnits?: string;
  /** Shows the duration with a trailing "?". */
  estimated?: boolean;
  /** The name of a Project.calendars entry. */
  calendar?: string;
  /** Plain text, stored as RTF. */
  notes?: string;
  wbs?: string;
  /** ASAP ALAP MSO MFO SNET SNLT FNET FNLT. */
  constraint?: string;
  constraintDate?: Date;
  deadline?: Date;
  percentComplete?: number;
  /** 0-1000, 500 = normal. */
  priority?: number;
  taskType?: "fixed_units" | "fixed_duration" | "fixed_work";
  effortDriven?: boolean;
  manual?: boolean;
  text?: Record<number, string>;
  number?: Record<number, number>;
  date?: Record<number, Date>;
  flag?: Record<number, boolean>;
  /** Pass your own to keep GUIDs stable between exports. */
  guid?: Uint8Array;
}

export interface Relation {
  predUid: number;
  succUid: number;
  type?: "FS" | "SS" | "FF" | "SF";
  lagDays?: number;
}

export interface CalendarException {
  /** Non-working from this date... */
  start: Date;
  /** ...to this one (defaults to start). */
  finish?: Date;
  name?: string;
}

export interface Calendar {
  name?: string;
  /**
   * weekday (0 = Monday .. 6 = Sunday) -> working ranges as
   * [startMinute, endMinute] from midnight, or null for non-working. Missing
   * days keep Project's defaults.
   */
  week?: Record<number, Array<[number, number]> | null>;
  exceptions?: CalendarException[];
  guid?: Uint8Array;
}

export interface Resource {
  uid: number;
  name: string;
  initials?: string;
  email?: string;
  /** 1.0 = 100%. */
  maxUnits?: number;
  guid?: Uint8Array;
}

export interface Assignment {
  taskUid: number;
  resourceUid: number;
  /** 1.0 = 100%. */
  units?: number;
}

export interface Project {
  title: string;
  start: Date;
  tasks: Task[];
  relations?: Relation[];
  resources?: Resource[];
  assignments?: Assignment[];
  /** Edits applied to the Standard calendar. */
  calendar?: Calendar;
  /** Extra base calendars. */
  calendars?: Calendar[];
  defaultCalendar?: string;
  author?: string;
  subject?: string;
  keywords?: string;
  comments?: string;
  manager?: string;
  company?: string;
  category?: string;
  statusDate?: Date;
  currencySymbol?: string;
  currencyCode?: string;
}

/** A schedule Microsoft Project accepts but will not reproduce as written. */
export class ScheduleWarning extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ScheduleWarning";
  }
}

// ----------------------------------------------------------- scheduling ---

export interface WorkPattern {
  /** python weekday (0 = Monday) -> working ranges */
  windows: Map<number, Array<[number, number]>>;
  /** days that are non-working whatever the week says, as yyyy-mm-dd */
  nonworking: Set<string>;
}

const dayKey = (d: Date) => d.toISOString().slice(0, 10);
/** JS getUTCDay is Sunday-based; the model counts weekdays from Monday. */
const weekdayOf = (d: Date) => (d.getUTCDay() + 6) % 7;
const minutesOf = (d: Date) => d.getUTCHours() * 60 + d.getUTCMinutes();
const atMinutes = (d: Date, minutes: number) =>
  new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), Math.floor(minutes / 60), minutes % 60));

export function workPattern(cal?: Calendar | null): WorkPattern {
  const windows = new Map<number, Array<[number, number]>>();
  for (let wd = 0; wd < 5; wd++) windows.set(wd, WORK_WINDOWS.map((w) => [...w] as [number, number]));
  const nonworking = new Set<string>();
  if (cal) {
    for (const [wdStr, val] of Object.entries(cal.week ?? {})) {
      const wd = Number(wdStr);
      if (val === null) windows.delete(wd);
      else windows.set(wd, val.map((w) => [...w] as [number, number]));
    }
    for (const ex of cal.exceptions ?? []) {
      let d = new Date(ex.start.getTime());
      const end = ex.finish ?? ex.start;
      while (d.getTime() <= end.getTime()) {
        nonworking.add(dayKey(d));
        d = new Date(d.getTime() + DAY_MS);
      }
    }
  }
  return { windows, nonworking };
}

const DEFAULT_PATTERN = (): WorkPattern => workPattern(null);

/** Working time between two datetimes, in tenths of a minute. */
export function workingTenths(start: Date, finish: Date, pattern?: WorkPattern): number {
  const { windows, nonworking } = pattern ?? DEFAULT_PATTERN();
  if (finish.getTime() <= start.getTime()) return 0;
  let total = 0;
  let day = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
  const lastDay = Date.UTC(finish.getUTCFullYear(), finish.getUTCMonth(), finish.getUTCDate());
  const startKey = dayKey(start);
  const finishKey = dayKey(finish);
  while (day.getTime() <= lastDay) {
    const key = dayKey(day);
    if (!nonworking.has(key)) {
      for (const [w0, w1] of windows.get(weekdayOf(day)) ?? []) {
        const lo = key === startKey ? Math.max(w0, minutesOf(start)) : w0;
        const hi = key === finishKey ? Math.min(w1, minutesOf(finish)) : w1;
        if (hi > lo) total += (hi - lo) * 10;
      }
    }
    day = new Date(day.getTime() + DAY_MS);
  }
  return total;
}

/** The instant reached after `tenths` of working time from `start`. */
export function advanceWorking(start: Date, tenths: number, pattern?: WorkPattern): Date {
  const { windows, nonworking } = pattern ?? DEFAULT_PATTERN();
  let minutes = Math.floor(tenths / 10);
  let day = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
  const point = minutesOf(start);
  const startKey = dayKey(start);
  for (let i = 0; i < 36600; i++) {
    const key = dayKey(day);
    if (!nonworking.has(key)) {
      for (const [w0, w1] of windows.get(weekdayOf(day)) ?? []) {
        const lo = key === startKey ? Math.max(w0, point) : w0;
        if (w1 > lo) {
          if (minutes <= w1 - lo) return atMinutes(day, lo + minutes);
          minutes -= w1 - lo;
        }
      }
    }
    day = new Date(day.getTime() + DAY_MS);
  }
  return start;
}

/**
 * The first instant at or after `point` that work can start. The end of a
 * working window is not a valid start — Project rolls 12:00 to 13:00, and the
 * end of a half day to the next morning.
 */
export function nextWorkingMoment(point: Date, pattern?: WorkPattern): Date {
  const { windows, nonworking } = pattern ?? DEFAULT_PATTERN();
  let day = new Date(Date.UTC(point.getUTCFullYear(), point.getUTCMonth(), point.getUTCDate()));
  const minute = minutesOf(point);
  const pointKey = dayKey(point);
  for (let i = 0; i < 3700; i++) {
    const key = dayKey(day);
    if (!nonworking.has(key)) {
      for (const [w0, w1] of [...(windows.get(weekdayOf(day)) ?? [])].sort((a, b) => a[0] - b[0])) {
        const lo = key === pointKey ? Math.max(w0, minute) : w0;
        if (lo < w1) return atMinutes(day, lo);
      }
    }
    day = new Date(day.getTime() + DAY_MS);
  }
  return point;
}

/**
 * The last working instant at or before `point` — where Project puts a task's
 * progress mark (native 387/1255), one working period behind its start.
 */
export function previousWorkingMoment(point: Date, pattern?: WorkPattern): Date | null {
  const { windows, nonworking } = pattern ?? DEFAULT_PATTERN();
  let day = new Date(Date.UTC(point.getUTCFullYear(), point.getUTCMonth(), point.getUTCDate()));
  const minute = minutesOf(point);
  const pointKey = dayKey(point);
  for (let i = 0; i < 3700; i++) {
    const key = dayKey(day);
    if (!nonworking.has(key)) {
      const sameDay = key === pointKey;
      const ends = (windows.get(weekdayOf(day)) ?? [])
        .filter(([, w1]) => !sameDay || w1 <= minute)
        .map(([, w1]) => w1);
      if (ends.length) return atMinutes(day, Math.max(...ends));
    }
    day = new Date(day.getTime() - DAY_MS);
  }
  return null;
}

/** Working minutes a normal week has in common between two work patterns. */
export function weeklyOverlapMinutes(a: WorkPattern, b: WorkPattern): number {
  let total = 0;
  for (let wd = 0; wd < 7; wd++) {
    for (const [a0, a1] of a.windows.get(wd) ?? []) {
      for (const [b0, b1] of b.windows.get(wd) ?? []) {
        total += Math.max(0, Math.min(a1, b1) - Math.max(a0, b0));
      }
    }
  }
  return total;
}

/**
 * Where a relation puts its successor's start, or null when it does not drive
 * the start (FF and SF constrain the finish instead).
 */
export function linkDrivenStart(
  rel: Relation,
  predEff: { start: Date; finish: Date },
  succDurTenths: number,
  pattern?: WorkPattern,
): Date | null {
  const lag = Math.round((rel.lagDays ?? 0) * TENTHS_PER_DAY);
  const type = rel.type ?? "FS";
  if (type === "FS") {
    // a zero-duration successor sits on the predecessor's finish; anything
    // longer starts at the next working moment after it
    if (lag === 0 && succDurTenths === 0) return predEff.finish;
    return nextWorkingMoment(advanceWorking(predEff.finish, lag, pattern), pattern);
  }
  if (type === "SS") {
    const s = lag ? advanceWorking(predEff.start, lag, pattern) : predEff.start;
    return succDurTenths === 0 ? s : nextWorkingMoment(s, pattern);
  }
  return null;
}

// ------------------------------------------------------------------ notes --

/** Wrap plain text in the minimal RTF envelope Project writes for notes. */
export function encodeRtfNotes(text: string): Uint8Array {
  let body = "";
  for (const ch of text) {
    if (ch === "\\" || ch === "{" || ch === "}") body += "\\" + ch;
    else if (ch === "\n") body += "\\par ";
    else if (ch.charCodeAt(0) > 127) body += `\\u${ch.charCodeAt(0)}?`;
    else body += ch;
  }
  const rtf =
    "{\\rtf1\\ansi\\ansicpg1252\\deff0\\nouicompat\\deflang1033" +
    "{\\fonttbl{\\f0\\fnil\\fcharset0 Segoe UI;}}\\viewkind4\\uc1 " +
    "\\pard\\f0\\fs20 " +
    body +
    "}";
  return Uint8Array.from(rtf, (c) => c.charCodeAt(0));
}

/** Plain text back out of that envelope. */
export function decodeRtfNotes(rtf: Uint8Array): string {
  const text = String.fromCharCode(...rtf);
  let body = text.split("\\fs20 ").pop() ?? "";
  if (body.endsWith("}")) body = body.slice(0, -1); // the group terminator, not an escape
  let out = "";
  let i = 0;
  while (i < body.length) {
    const ch = body[i]!;
    if (ch !== "\\") {
      out += ch;
      i += 1;
    } else if (body.startsWith("\\par ", i)) {
      out += "\n";
      i += 5;
    } else if (body.startsWith("\\u", i)) {
      let j = i + 2;
      let digits = "";
      while (j < body.length && (/\d/.test(body[j]!) || (body[j] === "-" && !digits))) digits += body[j++]!;
      out += digits ? String.fromCharCode(Number(digits)) : "";
      i = j < body.length && body[j] === "?" ? j + 1 : j;
    } else if (i + 1 < body.length) {
      out += body[i + 1]!;
      i += 2;
    } else {
      i += 1;
    }
  }
  return out.trim();
}

// -------------------------------------------------------------- validate --

/**
 * Reject structurally invalid projects before Project ever sees them:
 * duplicate or invalid uids, broken parent references, outline levels that do
 * not form a valid row-order outline, and dependency cycles.
 */
export function validate(project: Project): void {
  const seen = new Set<number>();
  for (const t of project.tasks) {
    if (t.uid <= 0) throw new Error(`task uid must be > 0 (got ${t.uid})`);
    if (seen.has(t.uid)) throw new Error(`duplicate task uid ${t.uid}`);
    seen.add(t.uid);
    if (t.finish.getTime() < t.start.getTime()) throw new Error(`task ${t.uid}: finish before start`);
  }
  // row-order outline: a level may rise by at most 1, and the parent must be
  // the nearest preceding task one level up
  const stack: Task[] = [];
  for (const t of project.tasks) {
    const level = t.outlineLevel ?? 1;
    if (level < 1) throw new Error(`task ${t.uid}: outlineLevel must be >= 1`);
    while (stack.length && (stack[stack.length - 1]!.outlineLevel ?? 1) >= level) stack.pop();
    const top = stack[stack.length - 1];
    const expectedParent = level > 1 && top ? top.uid : 0;
    if (level > 1 && (!top || (top.outlineLevel ?? 1) !== level - 1)) {
      throw new Error(
        `task ${t.uid}: outlineLevel ${level} does not follow a level ${level - 1} task`,
      );
    }
    if ((t.parentUid ?? 0) !== expectedParent) {
      throw new Error(
        `task ${t.uid}: parentUid ${t.parentUid ?? 0} does not match the outline ` +
          `(expected ${expectedParent})`,
      );
    }
    stack.push(t);
  }
  // relations: endpoints exist, no self-links, no cycles
  const succs = new Map<number, number[]>();
  for (const rel of project.relations ?? []) {
    for (const end of [rel.predUid, rel.succUid]) {
      if (!seen.has(end)) throw new Error(`relation references unknown task uid ${end}`);
    }
    if (rel.predUid === rel.succUid) throw new Error(`task ${rel.predUid} cannot depend on itself`);
    const list = succs.get(rel.predUid) ?? [];
    list.push(rel.succUid);
    succs.set(rel.predUid, list);
  }
  // depth-first with an explicit stack: a plan may chain further than the
  // JavaScript engine's own call stack allows
  const state = new Map<number, number>(); // 1 = on the current path, 2 = done
  for (const root of succs.keys()) {
    if (state.get(root)) continue;
    state.set(root, 1);
    const trail = [root];
    const stackIt: Array<[number, number[], number]> = [[root, succs.get(root) ?? [], 0]];
    while (stackIt.length) {
      const frame = stackIt[stackIt.length - 1]!;
      const [uid, kids] = frame;
      if (frame[2] >= kids.length) {
        state.set(uid, 2);
        stackIt.pop();
        trail.pop();
        continue;
      }
      const next = kids[frame[2]++]!;
      if (state.get(next) === 1) {
        const from = trail.indexOf(next);
        const cycle = [...trail.slice(from), next];
        throw new Error(`dependency cycle: ${cycle.join(" -> ")}`);
      }
      if (!state.get(next)) {
        state.set(next, 1);
        trail.push(next);
        stackIt.push([next, succs.get(next) ?? [], 0]);
      }
    }
  }
}
