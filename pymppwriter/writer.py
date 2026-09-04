"""Template-based MPP14 writer.

Strategy: start from a minimal .mpp saved by Microsoft Project (the template),
keep every stream we don't understand untouched, and regenerate only the
task / dependency data streams by cloning prototype records from the template
and patching the fields we control.
"""
from __future__ import annotations
import struct
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .cfb import load_cfb, write_cfb
from . import blocks as B

PROJECT_CLSID = uuid.UUID("74B78F3A-C8C8-11D1-BE11-00C04FB6FAF1").bytes_le
PRJ = "   114"
TASK_META_SIZE, TASK_META2_SIZE = 47, 92
REL_META_SIZE, REL_META2_SIZE = 10, 9
RSC_META_SIZE = 37
ASSN_META_SIZE = 34
CAL_META_SIZE = 10
TENTHS_PER_DAY = 4800          # 8h * 60m * 10
NATIVE = {"UNIQUE_ID": 86, "ID": 23, "NAME": 14, "START": 35, "FINISH": 36, "DURATION": 29,
          "REMAINING_DURATION": 31, "OUTLINE_LEVEL": 249, "PARENT_UID": 160, "EARLY_START": 37,
          "EARLY_FINISH": 38, "LATE_START": 39, "LATE_FINISH": 40, "CREATED": 93, "GUID": 1143,
          "MILESTONE": 24, "SUMMARY": 92, "ESTIMATED": 396, "ACTUAL_DURATION_UNITS": 181,
          "TASK_MODE": 1280, "WORK": 0, "REMAINING_WORK": 4, "CALENDAR_UNIQUE_ID": 401,
          "MANUAL_START": 1283, "MANUAL_FINISH": 1284, "MANUAL_DURATION": 1288,
          "MANUAL_DURATION_UNITS": 1289,
          "MANUALLY_SCHEDULED": 1408,   # the flag M365 actually reads; 1280 stays set either way
          "NOTES": 15, "WBS": 16, "CONSTRAINT_TYPE": 17, "CONSTRAINT_DATE": 18, "DEADLINE": 437,
          "PERCENT_COMPLETE": 32, "PERCENT_WORK_COMPLETE": 33, "ACTUAL_START": 41,
          "ACTUAL_FINISH": 42, "ACTUAL_DURATION": 28, "ACTUAL_WORK": 2, "STOP": 100,
          "RESUME": 99, "PRIORITY": 25, "TYPE": 128, "EFFORT_DRIVEN": 132,
          "SUMMARY_PROGRESS": 387, "SUMMARY_PROGRESS_PRIOR": 1255}
# custom field native ids, index 0 = Text1/Number1/Date1/Flag1
TEXT_IDS = [51, 54, 57, 60, 63, 66, 67, 68, 69, 70, 317, 318, 319, 320, 321,
            322, 323, 324, 325, 326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 336]
NUMBER_IDS = [87, 88, 89, 90, 91, 302, 303, 304, 305, 306, 307, 308, 309, 310,
              311, 312, 313, 314, 315, 316]
DATE_IDS = [265, 266, 267, 268, 269, 270, 271, 272, 273, 274]
FLAG_IDS = [72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 292, 293, 294, 295, 296,
            297, 298, 299, 300, 301]
CONSTRAINT_TYPES = {"ASAP": 0, "ALAP": 1, "MSO": 2, "MFO": 3,
                    "SNET": 4, "SNLT": 5, "FNET": 6, "FNLT": 7}
TASK_TYPES = {"fixed_units": 0, "fixed_duration": 1, "fixed_work": 2}
CAL_NAME_VAR, CAL_DATA_VAR = 1, 8
RSC_NATIVE = {"UNIQUE_ID": 27, "ID": 0, "NAME": 1, "INITIALS": 2, "EMAIL_ADDRESS": 35,
              "MAX_UNITS": 4, "CALENDAR_UID": 56, "GUID": 728, "CALENDAR_GUID": 729,
              "POSITION": 730}
ASSN_NATIVE = {"UNIQUE_ID": 0, "TASK_UNIQUE_ID": 1, "RESOURCE_UNIQUE_ID": 2, "START": 20,
               "FINISH": 21, "RESUME": 24, "STOP": 264, "UNITS": 7, "WORK": 8,
               "ACTUAL_WORK": 10, "REGULAR_WORK": 11, "REMAINING_WORK": 12, "GUID": 636,
               "TASK_GUID": 637, "RESOURCE_GUID": 638, "CREATED": 634, "PLANNED_WORK_DATA": 49}
REL_TYPES = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}
PCT_SCALE = 10000.0            # resource max units / assignment units: 10000.0 = 100%
WORK_SCALE = 100.0             # work doubles are minutes*1000 = duration tenths * 100
UNITS_CODES = {"m": 3, "h": 5, "d": 7, "w": 9, "mo": 11}
SUMMARY_UNITS = 0x15           # what Project writes in the units word of summary rows
ESTIMATED_FLAG = 0x20          # OR'ed into the units word; shows as "3 days?"
WORK_WINDOWS = ((8 * 60, 12 * 60), (13 * 60, 17 * 60))   # Standard calendar, minutes from midnight


def working_tenths(start: datetime, finish: datetime, pattern=None) -> int:
    """Working time between two datetimes in tenths of a minute.

    pattern is (windows, nonworking_dates) from _work_pattern(); the default is
    the Standard calendar (Mon-Fri, 08:00-12:00 and 13:00-17:00)."""
    windows, nonworking = pattern if pattern else ({wd: WORK_WINDOWS for wd in range(5)}, frozenset())
    if finish <= start:
        return 0
    total, day = 0, start.date()
    while day <= finish.date():
        if day not in nonworking:
            for w0, w1 in windows.get(day.weekday(), ()):
                lo = max(w0, start.hour * 60 + start.minute) if day == start.date() else w0
                hi = min(w1, finish.hour * 60 + finish.minute) if day == finish.date() else w1
                if hi > lo:
                    total += (hi - lo) * 10
        day += timedelta(days=1)
    return total


def encode_rtf_notes(text: str) -> bytes:
    """Wrap plain text in the minimal RTF envelope Project writes for notes."""
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\par ")
        elif ord(ch) > 127:
            out.append(f"\\u{ord(ch)}?")
        else:
            out.append(ch)
    return ("{\\rtf1\\ansi\\ansicpg1252\\deff0\\nouicompat\\deflang1033"
            "{\\fonttbl{\\f0\\fnil\\fcharset0 Segoe UI;}}\\viewkind4\\uc1 "
            "\\pard\\f0\\fs20 " + "".join(out) + "}").encode("ascii")


def advance_working(start: datetime, tenths: int, pattern=None) -> datetime:
    """The datetime reached after `tenths` of working time from `start`."""
    windows, nonworking = pattern if pattern else ({wd: WORK_WINDOWS for wd in range(5)}, frozenset())
    minutes = tenths // 10
    day, point = start.date(), start.hour * 60 + start.minute
    for _ in range(36600):
        if day not in nonworking:
            for w0, w1 in windows.get(day.weekday(), ()):
                lo = max(w0, point) if day == start.date() else w0
                if w1 > lo:
                    if minutes <= w1 - lo:
                        m = lo + minutes
                        return datetime(day.year, day.month, day.day, m // 60, m % 60)
                    minutes -= w1 - lo
        day += timedelta(days=1)
    return start


def previous_working_moment(point: datetime, pattern=None) -> Optional[datetime]:
    """The last working instant at or before `point` — where Project puts a
    task's progress mark (native 387), one working period behind its start."""
    windows, nonworking = pattern if pattern else ({wd: WORK_WINDOWS for wd in range(5)}, frozenset())
    day, minute = point.date(), point.hour * 60 + point.minute
    for _ in range(3700):
        if day not in nonworking:
            ends = [w1 for w0, w1 in windows.get(day.weekday(), ())
                    if day < point.date() or w1 <= minute]
            if ends:
                m = max(ends)
                return datetime(day.year, day.month, day.day, m // 60, m % 60)
        day -= timedelta(days=1)
    return None


def next_working_moment(point: datetime, pattern=None) -> datetime:
    """The first instant at or after `point` that work can start. The end of a
    working window is not a valid start — Project rolls 12:00 to 13:00, and the
    end of a half day to the next morning."""
    windows, nonworking = pattern if pattern else ({wd: WORK_WINDOWS for wd in range(5)}, frozenset())
    day, minute = point.date(), point.hour * 60 + point.minute
    for _ in range(3700):
        if day not in nonworking:
            for w0, w1 in sorted(windows.get(day.weekday(), ())):
                lo = max(w0, minute) if day == point.date() else w0
                if lo < w1:
                    return datetime(day.year, day.month, day.day, lo // 60, lo % 60)
        day += timedelta(days=1)
    return point


@dataclass
class Task:
    uid: int
    name: str
    start: datetime
    finish: datetime
    duration_days: float = 1.0     # ignored for summary tasks (rolled up from children)
    outline_level: int = 1
    parent_uid: int = 0            # 0 = project summary task
    duration_units: str = "d"      # display units: m, h, d, w, mo
    estimated: bool = False        # True shows the duration with a trailing "?"
    calendar: Optional[str] = None     # name of a Project.calendars entry
    notes: str = ""                # plain text, stored as RTF
    wbs: Optional[str] = None      # custom WBS code; None = Project derives outline numbers
    constraint: Optional[str] = None   # ASAP ALAP MSO MFO SNET SNLT FNET FNLT
    constraint_date: Optional[datetime] = None
    deadline: Optional[datetime] = None
    percent_complete: int = 0
    priority: int = 500            # 0-1000, 500 = normal
    task_type: str = "fixed_units"     # fixed_units | fixed_duration | fixed_work
    effort_driven: bool = False
    manual: bool = False           # manually scheduled
    text: Dict[int, str] = field(default_factory=dict)       # Text1-30
    number: Dict[int, float] = field(default_factory=dict)   # Number1-20
    date: Dict[int, datetime] = field(default_factory=dict)  # Date1-10
    flag: Dict[int, bool] = field(default_factory=dict)      # Flag1-20
    guid: bytes = field(default_factory=lambda: uuid.uuid4().bytes_le)


@dataclass
class Relation:
    pred_uid: int
    succ_uid: int
    type: str = "FS"
    lag_days: float = 0.0


@dataclass
class CalendarException:
    start: "date"                  # datetime.date; non-working (holiday)
    finish: Optional["date"] = None    # defaults to start
    name: str = ""


@dataclass
class Calendar:
    """A working-week definition. week maps python weekday (0=Mon .. 6=Sun) to
    None (non-working) or a list of (start_minute, end_minute) ranges; missing
    days keep Project's defaults (Mon-Fri 08:00-12:00, 13:00-17:00)."""
    name: str = "Standard"
    week: Dict[int, Optional[List[tuple]]] = field(default_factory=dict)
    exceptions: List[CalendarException] = field(default_factory=list)
    guid: bytes = field(default_factory=lambda: uuid.uuid4().bytes_le)

    def day_blocks(self):
        """7 (day_type, ranges) tuples, Sunday first, for build_calendar_data."""
        out = []
        for block in range(7):
            wd = (block + 6) % 7           # block 0 = Sunday = python weekday 6
            if wd not in self.week:
                out.append((B.CAL_DAY_DEFAULT, ()))
            elif self.week[wd] is None:
                out.append((B.CAL_DAY_NONWORKING, ()))
            else:
                out.append((B.CAL_DAY_WORKING, self.week[wd]))
        return out

    def exception_tuples(self):
        return sorted(((x.start, x.finish or x.start, x.name) for x in self.exceptions),
                      key=lambda t: t[0])


def _work_pattern(cal: Optional[Calendar]):
    """(windows, nonworking_dates) for working_tenths, from a Calendar."""
    windows = {wd: list(WORK_WINDOWS) for wd in range(5)}
    nonworking = set()
    if cal is not None:
        for wd, val in cal.week.items():
            if val is None:
                windows.pop(wd, None)
            else:
                windows[wd] = list(val)
        for x in cal.exceptions:
            d, end = x.start, x.finish or x.start
            while d <= end:
                nonworking.add(d)
                d += timedelta(days=1)
    return windows, nonworking


@dataclass
class Resource:
    uid: int                       # unique, > 0
    name: str
    initials: str = ""
    email: str = ""
    max_units: float = 1.0         # 1.0 = 100%
    guid: bytes = field(default_factory=lambda: uuid.uuid4().bytes_le)


@dataclass
class Assignment:
    task_uid: int
    resource_uid: int
    units: float = 1.0             # 1.0 = 100%


@dataclass
class Project:
    title: str
    start: datetime
    tasks: List[Task] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)
    resources: List[Resource] = field(default_factory=list)
    assignments: List[Assignment] = field(default_factory=list)
    calendar: Optional[Calendar] = None       # edits applied to the Standard calendar
    calendars: List[Calendar] = field(default_factory=list)   # extra base calendars
    default_calendar: Optional[str] = None    # project calendar name; default Standard
    author: Optional[str] = None              # document metadata (SummaryInformation)
    subject: Optional[str] = None
    keywords: Optional[str] = None
    comments: Optional[str] = None
    manager: Optional[str] = None             # DocumentSummaryInformation
    company: Optional[str] = None
    category: Optional[str] = None
    status_date: Optional[datetime] = None
    currency_symbol: Optional[str] = None     # e.g. "£"; default template's ("$")
    currency_code: Optional[str] = None       # e.g. "GBP"


class ScheduleWarning(UserWarning):
    """A declared date Project's scheduler will not agree with."""


def weekly_overlap_minutes(a_pattern, b_pattern) -> int:
    """Working minutes a normal week has in common between two work patterns."""
    (a_windows, _), (b_windows, _) = a_pattern, b_pattern
    total = 0
    for wd in range(7):
        for a0, a1 in a_windows.get(wd, ()):
            for b0, b1 in b_windows.get(wd, ()):
                total += max(0, min(a1, b1) - max(a0, b0))
    return total


def link_driven_start(rel: Relation, pred_eff, succ_dur_tenths: int, pattern):
    """Where a relation puts its successor's start, or None if it does not
    drive the start (FF and SF constrain the finish instead)."""
    p_start, p_finish = pred_eff[0], pred_eff[1]
    lag = int(round(rel.lag_days * TENTHS_PER_DAY))
    if rel.type == "FS":
        # a zero-duration successor sits on the predecessor's finish; anything
        # longer starts at the next working moment after it
        if lag == 0 and succ_dur_tenths == 0:
            return p_finish
        return next_working_moment(advance_working(p_finish, lag, pattern), pattern)
    if rel.type == "SS":
        s = advance_working(p_start, lag, pattern) if lag else p_start
        return s if succ_dur_tenths == 0 else next_working_moment(s, pattern)
    return None


def validate(project: Project) -> None:
    """Reject structurally invalid projects before Project ever sees them:
    duplicate/invalid uids, broken parent references, outline levels that
    do not form a valid row-order outline, and dependency cycles."""
    seen = set()
    for t in project.tasks:
        if t.uid <= 0:
            raise ValueError(f"task uid must be > 0 (got {t.uid})")
        if t.uid in seen:
            raise ValueError(f"duplicate task uid {t.uid}")
        seen.add(t.uid)
        if t.finish < t.start:
            raise ValueError(f"task {t.uid}: finish before start")
    # row-order outline: level may rise by at most 1, and the parent must be
    # the nearest preceding task one level up (level 1 tasks have parent 0)
    stack: List[Task] = []
    for t in project.tasks:
        if t.outline_level < 1:
            raise ValueError(f"task {t.uid}: outline_level must be >= 1")
        while stack and stack[-1].outline_level >= t.outline_level:
            stack.pop()
        expected_parent = stack[-1].uid if t.outline_level > 1 else 0
        if t.outline_level > 1 and (not stack or stack[-1].outline_level != t.outline_level - 1):
            raise ValueError(f"task {t.uid}: outline_level {t.outline_level} does not follow "
                             f"a level {t.outline_level - 1} task")
        if t.parent_uid != expected_parent:
            raise ValueError(f"task {t.uid}: parent_uid {t.parent_uid} does not match the "
                             f"outline (expected {expected_parent})")
        stack.append(t)
    # relations: endpoints exist, no self-links, no cycles
    succs: Dict[int, List[int]] = {}
    for rel in project.relations:
        for end in (rel.pred_uid, rel.succ_uid):
            if end not in seen:
                raise ValueError(f"relation references unknown task uid {end}")
        if rel.pred_uid == rel.succ_uid:
            raise ValueError(f"task {rel.pred_uid} cannot depend on itself")
        succs.setdefault(rel.pred_uid, []).append(rel.succ_uid)
    # depth-first search with an explicit stack: a plan may chain far more
    # tasks than Python's recursion limit allows
    state: Dict[int, int] = {}    # 0/absent=new, 1=on the current path, 2=done
    for root in succs:
        if state.get(root, 0):
            continue
        state[root] = 1
        trail = [root]
        stack = [(root, iter(succs[root]))]
        while stack:
            uid, pending = stack[-1]
            nxt = next(pending, None)
            if nxt is None:
                state[uid] = 2
                stack.pop()
                trail.pop()
            elif state.get(nxt) == 1:      # still on the current path = cycle
                cycle = trail[trail.index(nxt):] + [nxt]
                raise ValueError(f"dependency cycle: {' -> '.join(map(str, cycle))}")
            elif state.get(nxt, 0) == 0:
                state[nxt] = 1
                trail.append(nxt)
                stack.append((nxt, iter(succs.get(nxt, ()))))


class MppWriter:
    def __init__(self, template_path: str):
        self.root = load_cfb(template_path)
        self.prj = self.root.storage_path(PRJ)
        hdr, props, order = B.parse_props(self._get(f"{PRJ}/Props"))
        self.props_hdr, self.props, self.props_order = hdr, props, order
        self.task_fm, self.task_bit = self._parse_class_map(B.PROPS_TASK_FIELD_MAP)
        self.rsc_fm, self.rsc_bit = self._parse_class_map(B.PROPS_RESOURCE_FIELD_MAP)
        self.assn_fm, self.assn_bit = self._parse_class_map(B.PROPS_ASSIGNMENT_FIELD_MAP)
        self.template_start = self.props.get(B.PROPS_PROJECT_START_DATE, b"")
        rel_fm, _ = self._parse_class_map(B.PROPS_RELATION_FIELD_MAP)
        # the unmapped relation trailer moved between eras: 2010 files (native
        # id 9 at offset 0) use type@12, lagUnits@14, lag@16; M365 files use
        # type@12, lag@14, lagUnits@18 (verified against Project-written files)
        self.rel_2010_layout = rel_fm.get(9) is not None and rel_fm[9].offset == 0
        self._load_prototypes()

    # ------------------------------------------------------------ helpers --
    def _parse_class_map(self, props_key: int):
        """fm: native id -> fixed FieldItem; bit: native id -> field map entry index
        (= bit index in the FixedMeta/Fixed2Meta bitmap)."""
        fm, bit = {}, {}
        for i, it in enumerate(B.parse_field_map(self.props[props_key])):
            tid = it.type_value & 0xFFFF
            if it.in_fixed:
                fm.setdefault(tid, it)
            bit.setdefault(tid, i)
        return fm, bit

    def _get(self, path: str) -> bytes:
        s = self.root
        parts = path.split("/")
        for p in parts[:-1]:
            s = s.children[p]
        return s.children[parts[-1]]

    def _set(self, path: str, data: bytes) -> None:
        self.root.set_path(path, data)

    # block-aware field writers for any entity class (fm = that class's fixed map)
    @staticmethod
    def _putf(fm: dict, native: dict, rec: bytearray, rec2: bytearray, name: str, fmt: str, value) -> None:
        it = fm.get(native[name])
        if it is None:
            return
        dst = rec if it.block == 0 else rec2
        if it.offset + struct.calcsize(fmt) <= len(dst):
            struct.pack_into(fmt, dst, it.offset, value)

    @staticmethod
    def _putf_bytes(fm: dict, native: dict, rec: bytearray, rec2: bytearray, name: str, value: bytes) -> None:
        it = fm.get(native[name])
        if it is None:
            return
        dst = rec if it.block == 0 else rec2
        if it.offset + len(value) <= len(dst):
            dst[it.offset:it.offset + len(value)] = value

    @classmethod
    def _putf_ts(cls, fm: dict, native: dict, rec: bytearray, rec2: bytearray, name: str,
                 dt: Optional[datetime]) -> None:
        cls._putf_bytes(fm, native, rec, rec2, name, B.encode_timestamp(dt))

    @staticmethod
    def _bitf(bits: dict, native: dict, meta: bytearray, meta2: bytearray, name: str, value: bool) -> None:
        idx = bits.get(native[name])
        if idx is not None:
            B.set_meta_bit(meta, meta2, idx, value)

    def _meta(self, path: str, default_size: int):
        return B.parse_fixed_meta_auto(self._get(path), default_size)

    def _load_prototypes(self) -> None:
        t = f"{PRJ}/TBkndTask"
        mh, _, mitems = self._meta(f"{t}/FixedMeta", TASK_META_SIZE)
        recs = B.split_fixed_data(self._get(f"{t}/FixedData"), mitems)
        m2h, _, m2items = self._meta(f"{t}/Fixed2Meta", TASK_META2_SIZE)
        recs2 = B.split_fixed_data(self._get(f"{t}/Fixed2Data"), m2items)
        vh, vtable, _ = B.parse_var_meta(self._get(f"{t}/VarMeta"))
        vdata = self._get(f"{t}/Var2Data")
        self.task_meta_hdr, self.task_meta2_hdr, self.task_var_hdr = mh, m2h, vh
        # prototypes: the uid-0 project summary, and the first real LEAF task
        # (in the recipe template, Task 1 is itself a summary — skip it)
        full = [i for i, r in enumerate(recs) if len(r) > 100]
        if not full:
            raise ValueError("template has no task records to use as prototypes")
        summary_i = full[0]
        sum_bit = self.task_bit.get(NATIVE["SUMMARY"])
        task_i = next((i for i in full[1:]
                       if sum_bit is None or not B.meta_bit(mitems[i], m2items[i], sum_bit)),
                      full[-1] if len(full) > 1 else full[0])
        self.proto = {}
        for label, i in (("summary", summary_i), ("task", task_i)):
            uid = struct.unpack_from("<I", recs[i], 0)[0]
            var = [(typ, B.read_var(vdata, off)) for typ, off in sorted(vtable.get(uid, {}).items())]
            self.proto[label] = dict(rec=recs[i], rec2=recs2[i], meta=mitems[i], meta2=m2items[i], var=var)
        # deleted/null-task stubs at the front of the block (kept verbatim)
        self.stubs = [(recs[i], recs2[i], mitems[i], m2items[i]) for i in range(len(recs)) if len(recs[i]) <= 16]
        # relations
        c = f"{PRJ}/TBkndCons"
        rmh, _, rmitems = self._meta(f"{c}/FixedMeta", REL_META_SIZE)
        rrecs = B.split_fixed_data(self._get(f"{c}/FixedData"), rmitems)
        rm2h, _, rm2items = self._meta(f"{c}/Fixed2Meta", REL_META2_SIZE)
        rrecs2 = B.split_fixed_data(self._get(f"{c}/Fixed2Data"), rm2items)
        self.rel_meta_hdr, self.rel_meta2_hdr = rmh, rm2h
        self.rel_proto = None
        if rrecs and len(rrecs[0]) >= 20:
            self.rel_proto = dict(rec=rrecs[0], rec2=rrecs2[0], meta=rmitems[0], meta2=rm2items[0])
        # assignments: the template's phantom per-task records (Project creates one
        # unassigned assignment per task) are never emitted as-is — they override
        # task durations when joined by task UID — but the first one is the
        # prototype for real assignments
        a = f"{PRJ}/TBkndAssn"
        amh, _, amitems = self._meta(f"{a}/FixedMeta", ASSN_META_SIZE)
        arecs = B.split_fixed_data(self._get(f"{a}/FixedData"), amitems)
        am2h, _, am2items = self._meta(f"{a}/Fixed2Meta", 53)
        arecs2 = B.split_fixed_data(self._get(f"{a}/Fixed2Data"), am2items)
        avh, avtable, _ = B.parse_var_meta(self._get(f"{a}/VarMeta"))
        avdata = self._get(f"{a}/Var2Data")
        self.assn_meta_hdr, self.assn_meta2_hdr, self.assn_var_hdr = amh[:16], am2h[:16], avh[:24]
        self.assn_proto = None
        task_it = self.assn_fm.get(ASSN_NATIVE["TASK_UNIQUE_ID"])
        for i, rec in enumerate(arecs):
            if len(rec) > 50 and i < len(arecs2):
                # skip the project-summary placeholder (task uid 0): it carries
                # no var data, so it lacks the planned-work contour prototype
                if task_it is not None and struct.unpack_from("<I", rec, task_it.offset)[0] == 0:
                    continue
                uid = struct.unpack_from("<I", rec, 0)[0]
                var = [(typ, B.read_var(avdata, off)) for typ, off in sorted(avtable.get(uid, {}).items())]
                self.assn_proto = dict(rec=rec, rec2=arecs2[i], meta=amitems[i], meta2=am2items[i], var=var)
                break
        # resources: the uid-0 "Unassigned" system record (present even in a blank
        # project) is the prototype for real resource records
        rs = f"{PRJ}/TBkndRsc"
        rsmh, _, rsmitems = self._meta(f"{rs}/FixedMeta", RSC_META_SIZE)
        rsrecs = B.split_fixed_data(self._get(f"{rs}/FixedData"), rsmitems)
        rsm2h, _, rsm2items = self._meta(f"{rs}/Fixed2Meta", 50)
        rsrecs2 = B.split_fixed_data(self._get(f"{rs}/Fixed2Data"), rsm2items)
        rsvh, rsvtable, _ = B.parse_var_meta(self._get(f"{rs}/VarMeta"))
        rsvdata = self._get(f"{rs}/Var2Data")
        self.rsc_meta_hdr, self.rsc_meta2_hdr, self.rsc_var_hdr = rsmh[:16], rsm2h[:16], rsvh[:24]
        self.rsc_rows = []       # (rec, rec2, meta, meta2, [(var type, payload)]) for stubs + uid 0
        self.rsc_proto = None
        for i, rec in enumerate(rsrecs):
            uid = struct.unpack_from("<I", rec, 0)[0] if len(rec) >= 4 else 0
            var = [(typ, B.read_var(rsvdata, off)) for typ, off in sorted(rsvtable.get(uid, {}).items())] \
                if len(rec) > 16 else []
            row = dict(rec=rec, rec2=rsrecs2[i] if i < len(rsrecs2) else b"",
                       meta=rsmitems[i], meta2=rsm2items[i] if i < len(rsm2items) else b"", var=var)
            self.rsc_rows.append(row)
            if len(rec) > 100 and uid == 0:
                self.rsc_proto = row
        # calendars: keep every existing record; clone the uid-0 resource's calendar
        # for new resources. The three dwords of a 12-byte calendar record are
        # (calendar uid, base calendar uid, resource uid) in an order that varies by
        # Project version, so detect the columns from the template's own records.
        cl = f"{PRJ}/TBkndCal"
        clmh, _, clmitems = self._meta(f"{cl}/FixedMeta", CAL_META_SIZE)
        clrecs = B.split_fixed_data(self._get(f"{cl}/FixedData"), clmitems)
        clm2h, _, clm2items = self._meta(f"{cl}/Fixed2Meta", 9)
        clrecs2 = B.split_fixed_data(self._get(f"{cl}/Fixed2Data"), clm2items)
        self.cal_meta_hdr, self.cal_meta2_hdr = clmh[:16], clm2h[:16]
        clvraw = self._get(f"{cl}/VarMeta")
        clvh, clvtable, _ = B.parse_var_meta(clvraw)
        clvdata = self._get(f"{cl}/Var2Data")
        self.cal_var_hdr = clvh
        self.cal_var_hi = struct.unpack_from("<H", clvraw, 34)[0] if len(clvraw) >= 36 else 0
        self.cal_var_entries = [(uid, typ, B.read_var(clvdata, off))
                                for uid, d in clvtable.items() for typ, off in d.items()]
        self.cal_rows = [dict(rec=clrecs[i], rec2=clrecs2[i] if i < len(clrecs2) else b"",
                              meta=clmitems[i], meta2=clm2items[i] if i < len(clm2items) else b"")
                         for i in range(len(clrecs))]
        self.cal_proto = self.cal_cols = self.cal_standard_uid = self.cal_standard_guid = None
        self.cal_base_row = self.cal_uid_col = None
        # column order varies by Project vintage, so anchor on the uid-0
        # resource's calendar row: its three values — its own calendar uid
        # (from the resource record's CALENDAR_UID field), the Standard uid,
        # and resource uid 0 — are distinct and identify each column
        rsc0_cal_uid = None
        if self.rsc_proto is not None:
            it = self.rsc_fm.get(RSC_NATIVE["CALENDAR_UID"])
            if it is not None:
                src = self.rsc_proto["rec"] if it.block == 0 else self.rsc_proto["rec2"]
                if it.offset + 4 <= len(src):
                    rsc0_cal_uid = struct.unpack_from("<i", src, it.offset)[0]
        for row in self.cal_rows:
            if len(row["rec"]) != 12 or not rsc0_cal_uid:
                continue
            d = struct.unpack("<3i", row["rec"])
            if rsc0_cal_uid in d and 0 in d:
                uid_col = d.index(rsc0_cal_uid)
                rsc_col = d.index(0)
                base_col = next(j for j in range(3) if j not in (uid_col, rsc_col))
                self.cal_cols = (uid_col, base_col, rsc_col)
                self.cal_uid_col = uid_col
                self.cal_standard_uid = d[base_col]
                self.cal_proto = row
                break
        if self.cal_cols is not None:
            for row in self.cal_rows:
                if len(row["rec"]) == 12 and row is not self.cal_proto:
                    d = struct.unpack("<3i", row["rec"])
                    if d[self.cal_uid_col] == self.cal_standard_uid:
                        self.cal_base_row = row
                        self.cal_standard_guid = row["rec2"][:16]
                        break

    def _put(self, rec: bytearray, name: str, fmt: str, value) -> None:
        it = self.task_fm.get(NATIVE[name])
        if it is None or it.block != 0:
            return
        struct.pack_into(fmt, rec, it.offset, value)

    def _put_ts(self, rec: bytearray, name: str, dt: Optional[datetime]) -> None:
        it = self.task_fm.get(NATIVE[name])
        if it is not None and it.block == 0:
            rec[it.offset:it.offset + 4] = B.encode_timestamp(dt)

    def _put_bit(self, meta: bytearray, meta2: bytearray, name: str, value: bool) -> None:
        idx = self.task_bit.get(NATIVE[name])
        if idx is not None:
            B.set_meta_bit(meta, meta2, idx, value)

    # ------------------------------------------------------------- build ---
    def build(self, project: Project) -> bytes:
        validate(project)
        by_uid: Dict[int, Task] = {t.uid: t for t in project.tasks}
        children: Dict[int, List[Task]] = {}
        for t in project.tasks:
            children.setdefault(t.parent_uid, []).append(t)

        # effective schedule per task: summaries roll up from children (working time),
        # deepest summaries first so rollups nest correctly
        def depth(t: Task) -> int:
            d, p, seen = 0, t.parent_uid, set()
            while p in by_uid and p not in seen:
                seen.add(p); d += 1; p = by_uid[p].parent_uid
            return d

        pattern = _work_pattern(project.calendar)
        eff: Dict[int, tuple] = {}    # uid -> (start, finish, dur_tenths)
        for t in sorted(project.tasks, key=depth, reverse=True):
            kids = children.get(t.uid)
            if kids:
                s = min(eff[k.uid][0] for k in kids)
                f = max(eff[k.uid][1] for k in kids)
                eff[t.uid] = (s, f, working_tenths(s, f, pattern))
            else:
                eff[t.uid] = (t.start, t.finish, int(round(t.duration_days * TENTHS_PER_DAY)))

        # earliest start each task's predecessors imply, so we only pin dates
        # the links do not already produce (Project's own files constrain
        # typed-in dates, not link-driven ones)
        link_start: Dict[int, datetime] = {}
        for rel in project.relations:
            if rel.pred_uid not in eff or rel.succ_uid not in eff:
                continue
            s = link_driven_start(rel, eff[rel.pred_uid], eff[rel.succ_uid][2], pattern)
            if s is None:
                continue                  # FF/SF do not drive the start directly
            prev = link_start.get(rel.succ_uid)
            if prev is None or s > prev:
                link_start[rel.succ_uid] = s
        # Project cannot put a resource to work on a task whose own calendar
        # shares no working time with the resource's: it drops the resource
        # calendar and pops "Not enough common working time" on open
        named_cals = {c.name: c for c in project.calendars}
        assigned = {a.task_uid for a in project.assignments}
        for t in project.tasks:
            cal = named_cals.get(t.calendar) if t.calendar else None
            if cal is not None and t.uid in assigned and not weekly_overlap_minutes(
                    _work_pattern(cal), pattern):
                warnings.warn(f"task {t.uid} {t.name!r} is on calendar {cal.name!r}, which shares "
                              f"no working time with the resource calendars; Project will schedule "
                              f"it ignoring the resource calendar", ScheduleWarning, stacklevel=2)

        # a start on a window boundary (12:00, or the end of a half day) is not
        # a working moment: Project rolls it forward on the next recalculation
        for t in project.tasks:
            if t.manual or children.get(t.uid) or eff[t.uid][2] == 0:
                continue           # manual, summary and milestone rows may sit on a boundary
            pat = pattern if t.calendar is None else _work_pattern(
                next((c for c in project.calendars if c.name == t.calendar), None))
            rolled = next_working_moment(eff[t.uid][0], pat)
            if rolled != eff[t.uid][0]:
                warnings.warn(f"task {t.uid} {t.name!r} starts {eff[t.uid][0]:%Y-%m-%d %H:%M}, which is "
                              f"not working time; Project will move it to {rolled:%Y-%m-%d %H:%M}",
                              ScheduleWarning, stacklevel=2)

        # a declared start earlier than the links allow is one Project will
        # move on its next recalculation: warn rather than write a schedule
        # that will not survive a round trip
        for uid, implied in link_start.items():
            task = by_uid[uid]
            if task.manual or children.get(uid) or task.calendar is not None:
                continue                  # manual, summary and other-calendar tasks differ
            if eff[uid][0] < implied:
                warnings.warn(f"task {uid} {task.name!r} starts {eff[uid][0]:%Y-%m-%d %H:%M} but "
                              f"its predecessors put it at {implied:%Y-%m-%d %H:%M}; "
                              f"Project will move it", ScheduleWarning, stacklevel=2)

        # assignment work per task (milli-minutes), rolled up into summaries
        for asn in project.assignments:
            if asn.task_uid not in by_uid:
                raise ValueError(f"assignment references unknown task uid {asn.task_uid}")
        direct_work: Dict[int, float] = {}
        for asn in project.assignments:
            dur_tenths = eff[asn.task_uid][2]
            direct_work[asn.task_uid] = direct_work.get(asn.task_uid, 0.0) + dur_tenths * WORK_SCALE * asn.units
        wsum: Dict[int, float] = {}
        for t in sorted(project.tasks, key=depth, reverse=True):
            wsum[t.uid] = direct_work.get(t.uid, 0.0) + sum(wsum[k.uid] for k in children.get(t.uid, []))

        # field validation + percent-complete rollup (summaries weighted by duration)
        pct_eff: Dict[int, int] = {}
        for t in sorted(project.tasks, key=depth, reverse=True):
            if t.constraint is not None and t.constraint not in CONSTRAINT_TYPES:
                raise ValueError(f"task {t.uid}: unknown constraint {t.constraint!r}")
            if t.task_type not in TASK_TYPES:
                raise ValueError(f"task {t.uid}: unknown task_type {t.task_type!r}")
            if not 0 <= t.percent_complete <= 100:
                raise ValueError(f"task {t.uid}: percent_complete out of range")
            for label, d, limit in (("text", t.text, 30), ("number", t.number, 20),
                                    ("date", t.date, 10), ("flag", t.flag, 20)):
                for n in d:
                    if not 1 <= n <= limit:
                        raise ValueError(f"task {t.uid}: {label}{n} out of range 1..{limit}")
            kids = children.get(t.uid)
            if kids:
                tot = sum(eff[k.uid][2] for k in kids)
                pct_eff[t.uid] = int(round(sum(eff[k.uid][2] * pct_eff[k.uid] for k in kids) / tot)) if tot else 0
            else:
                pct_eff[t.uid] = int(t.percent_complete)
        top_tot = sum(eff[t.uid][2] for t in project.tasks if t.parent_uid == 0)
        pct0 = int(round(sum(eff[t.uid][2] * pct_eff[t.uid] for t in project.tasks
                             if t.parent_uid == 0) / top_tot)) if top_tot else 0

        # project summary task (uid 0) spans all tasks
        p_start = min([eff[t.uid][0] for t in project.tasks] or [project.start])
        p_finish = max([eff[t.uid][1] for t in project.tasks] or [project.start])
        summary_guid = uuid.uuid4().bytes_le

        # calendars: uids for new base calendars, then rows + var entries.
        # Resource calendars allocated after these, in the resources section.
        std_uid = self.cal_standard_uid if self.cal_standard_uid is not None else 1
        existing_cal_uids = [std_uid]
        if self.cal_cols:
            existing_cal_uids = [struct.unpack("<3i", row["rec"])[self.cal_cols[0]]
                                 for row in self.cal_rows if len(row["rec"]) == 12]
        next_cal_uid = max(existing_cal_uids) + 1
        cal_rows_out = list(self.cal_rows)
        cal_meta_patched = False
        cal_var_new = []
        named_cal_uid = {"Standard": std_uid}
        if project.calendar is not None and (project.calendar.week or project.calendar.exceptions):
            cal_var_new.append((std_uid, CAL_DATA_VAR,
                                B.build_calendar_data(project.calendar.day_blocks(),
                                                      project.calendar.exception_tuples())))
            # the record's meta gates the var data: byte 2 counts the record's
            # var entries, trailing-byte bit 0x80 marks the data blob — without
            # them Project never reads the blob
            for i, row in enumerate(cal_rows_out):
                if row is self.cal_base_row:
                    m = bytearray(row["meta"])
                    m[2] += 1
                    m[8] |= 0xC0   # has-data flag: 0x80 in 2010-era metas, 0x40 in M365
                    cal_rows_out[i] = dict(row, meta=bytes(m))
                    cal_meta_patched = True
        for cal in project.calendars:
            if self.cal_base_row is None:
                raise ValueError("template has no base calendar record to clone")
            if cal.name in named_cal_uid:
                raise ValueError(f"duplicate calendar name {cal.name!r}")
            uid = next_cal_uid
            next_cal_uid += 1
            named_cal_uid[cal.name] = uid
            crec = bytearray(12)
            for j in range(3):
                struct.pack_into("<i", crec, j * 4, uid if j == self.cal_uid_col else -1)
            crec2 = bytearray(48)
            crec2[0:16] = cal.guid
            has_data = bool(cal.week or cal.exceptions)
            m = bytearray(self.cal_base_row["meta"])
            m[2] = 2 if has_data else 1        # var entry count: name (+ data blob)
            if has_data:
                m[8] |= 0xC0   # has-data flag: 0x80 in 2010-era metas, 0x40 in M365
            cal_rows_out.append(dict(rec=bytes(crec), rec2=bytes(crec2),
                                     meta=bytes(m),
                                     meta2=bytearray(self.cal_base_row["meta2"])))
            cal_var_new.append((uid, CAL_NAME_VAR, B.encode_unicode(cal.name)))
            if has_data:
                cal_var_new.append((uid, CAL_DATA_VAR,
                                    B.build_calendar_data(cal.day_blocks(), cal.exception_tuples())))

        fixed, fixed2, meta, meta2, var_entries = [], [], [], [], []
        for s in self.stubs:
            fixed.append(s[0]); fixed2.append(s[1]); meta.append(bytearray(s[2])); meta2.append(bytearray(s[3]))

        def emit(proto: dict, uid: int, tid: int, name: str, start, finish, dur_tenths: int,
                 level: int, parent_uid: int, guid: bytes, parent_guid: bytes, is_summary: bool,
                 position: int, units: str = "d", estimated: bool = False, work: float = 0.0,
                 cal_uid: Optional[int] = None, task: Optional[Task] = None, pct: int = 0):
            rec = bytearray(proto["rec"]); rec2 = bytearray(proto["rec2"])
            self._put(rec, "UNIQUE_ID", "<I", uid)
            self._put(rec, "ID", "<I", tid)
            self._put(rec, "OUTLINE_LEVEL", "<H", level)
            self._put(rec, "PARENT_UID", "<I", parent_uid)
            self._put(rec, "DURATION", "<i", dur_tenths)
            self._put(rec, "REMAINING_DURATION", "<i", dur_tenths)
            self._put(rec, "WORK", "<d", work)
            self._put(rec, "REMAINING_WORK", "<d", work)
            units_word = (SUMMARY_UNITS if is_summary else UNITS_CODES[units]) | (ESTIMATED_FLAG if estimated else 0)
            self._put(rec, "ACTUAL_DURATION_UNITS", "<H", units_word)
            for f in ("START", "EARLY_START", "LATE_START"):
                self._put_ts(rec, f, start)
            for f in ("FINISH", "EARLY_FINISH", "LATE_FINISH"):
                self._put_ts(rec, f, finish)
            self._put_ts(rec, "CREATED", datetime.now().replace(second=0, microsecond=0))
            # progress marks, or the template's own would be cloned onto every
            # row: M365 keeps the task's start in 387 and the working moment
            # before it in 1255; 2010-era files have only 387, holding that
            # earlier moment. Summary rows carry neither.
            if is_summary:
                self._put_ts(rec, "SUMMARY_PROGRESS", None)
                self._putf_ts(self.task_fm, NATIVE, rec, rec2, "SUMMARY_PROGRESS_PRIOR", None)
            else:
                mark = previous_working_moment(start, pattern)
                mark = max(mark, project.start) if mark else project.start
                if NATIVE["SUMMARY_PROGRESS_PRIOR"] in self.task_fm:
                    self._put_ts(rec, "SUMMARY_PROGRESS", start)
                    self._putf_ts(self.task_fm, NATIVE, rec, rec2, "SUMMARY_PROGRESS_PRIOR", mark)
                else:
                    self._put_ts(rec, "SUMMARY_PROGRESS", mark)
            rec2[0:16] = guid                 # task GUID (field map block 1, offset 0)
            struct.pack_into("<d", rec2, 16, float(position))
            rec2[24:40] = parent_guid         # parent task GUID
            # boolean task fields are bits in the FixedMeta/Fixed2Meta bitmap,
            # one bit per field-map entry, indexed by entry position
            m = bytearray(proto["meta"]); m2 = bytearray(proto["meta2"])
            self._put_bit(m, m2, "SUMMARY", is_summary)
            self._put_bit(m, m2, "MILESTONE", not is_summary and dur_tenths == 0)
            self._put_bit(m, m2, "ESTIMATED", estimated)
            # tasks are auto-scheduled unless asked; M365 templates default to manual
            manual = task.manual if task is not None else False
            self._put_bit(m, m2, "MANUALLY_SCHEDULED", manual)
            if manual:
                self._putf_ts(self.task_fm, NATIVE, rec, rec2, "MANUAL_START", start)
                self._putf_ts(self.task_fm, NATIVE, rec, rec2, "MANUAL_FINISH", finish)
                self._putf(self.task_fm, NATIVE, rec, rec2, "MANUAL_DURATION", "<i", dur_tenths)
                self._putf(self.task_fm, NATIVE, rec, rec2, "MANUAL_DURATION_UNITS", "<H",
                           UNITS_CODES[units])
            else:
                self._putf_ts(self.task_fm, NATIVE, rec, rec2, "MANUAL_START", None)
                self._putf_ts(self.task_fm, NATIVE, rec, rec2, "MANUAL_FINISH", None)
                self._putf(self.task_fm, NATIVE, rec, rec2, "MANUAL_DURATION", "<i", -1)
            if cal_uid is not None:
                self._put(rec, "CALENDAR_UNIQUE_ID", "<i", cal_uid)
                self._put_bit(m, m2, "CALENDAR_UNIQUE_ID", True)
            if task is not None:
                constraint, cdate = task.constraint, task.constraint_date
                if (constraint is None and not manual and not is_summary
                        and start > project.start and link_start.get(uid) != start):
                    # hold the declared start against the scheduling engine, the
                    # way Project itself pins a typed-in start date (otherwise an
                    # ASAP task snaps back to its earliest date on recalculation);
                    # tasks their predecessors already place stay ASAP
                    constraint, cdate = "SNET", start
                if constraint is not None:
                    self._put(rec, "CONSTRAINT_TYPE", "<H", CONSTRAINT_TYPES[constraint])
                    self._put_ts(rec, "CONSTRAINT_DATE", cdate)
                    self._put_bit(m, m2, "CONSTRAINT_TYPE", True)
                if task.deadline is not None:
                    self._put_ts(rec, "DEADLINE", task.deadline)
                    self._put_bit(m, m2, "DEADLINE", True)
                self._put(rec, "PRIORITY", "<H", task.priority)
                self._put(rec, "TYPE", "<H", TASK_TYPES[task.task_type])
                self._put_bit(m, m2, "EFFORT_DRIVEN", task.effort_driven)
            if pct:
                actdur = int(round(dur_tenths * pct / 100))
                self._put(rec, "PERCENT_COMPLETE", "<H", pct)
                self._put(rec, "PERCENT_WORK_COMPLETE", "<H", pct)
                self._put(rec, "ACTUAL_DURATION", "<i", actdur)
                self._put(rec, "REMAINING_DURATION", "<i", dur_tenths - actdur)
                self._put(rec, "ACTUAL_WORK", "<d", work * pct / 100.0)
                self._put(rec, "REMAINING_WORK", "<d", work * (100 - pct) / 100.0)
                self._put_ts(rec, "ACTUAL_START", start)
                point = finish if pct == 100 else advance_working(start, actdur, pattern)
                if pct == 100:
                    self._put_ts(rec, "ACTUAL_FINISH", finish)
                self._put_ts(rec, "STOP", point)
                self._put_ts(rec, "RESUME", point)
                for f in ("PERCENT_COMPLETE", "ACTUAL_START", "ACTUAL_DURATION"):
                    self._put_bit(m, m2, f, True)
            extra_vars = []
            if task is not None:
                if task.notes:
                    extra_vars.append((NATIVE["NOTES"], encode_rtf_notes(task.notes)))
                if task.wbs is not None:
                    extra_vars.append((NATIVE["WBS"], B.encode_unicode(task.wbs)))
                for n, v in sorted(task.text.items()):
                    extra_vars.append((TEXT_IDS[n - 1], B.encode_unicode(v)))
                for n, v in sorted(task.number.items()):
                    extra_vars.append((NUMBER_IDS[n - 1], struct.pack("<d", float(v))))
                for n, v in sorted(task.date.items()):
                    extra_vars.append((DATE_IDS[n - 1], B.encode_timestamp(v)))
                for n, v in sorted(task.flag.items()):
                    fbit = self.task_bit.get(FLAG_IDS[n - 1])
                    if fbit is not None:
                        B.set_meta_bit(m, m2, fbit, bool(v))
            for typ, _ in extra_vars:
                fbit = self.task_bit.get(typ)
                if fbit is not None:
                    B.set_meta_bit(m, m2, fbit, True)
            m[2] += len(extra_vars)            # meta byte 2 counts the record's var entries
            fixed.append(bytes(rec)); fixed2.append(bytes(rec2))
            meta.append(m); meta2.append(m2)
            for typ, payload in proto["var"]:
                if typ == NATIVE["NAME"]:
                    payload = B.encode_unicode(name)
                var_entries.append((uid, typ, payload))
            for typ, payload in extra_vars:
                var_entries.append((uid, typ, payload))

        emit(self.proto["summary"], 0, 0, project.title, p_start, p_finish,
             working_tenths(p_start, p_finish, pattern), 0, 0, summary_guid, b"\0" * 16, True, 1,
             work=sum(wsum[t.uid] for t in project.tasks if t.parent_uid == 0), pct=pct0)
        pos = 2
        # tasks in ID (display) order = list order
        for tid, t in enumerate(project.tasks, start=1):
            parent_guid = summary_guid if t.parent_uid == 0 else by_uid[t.parent_uid].guid
            start, finish, dur_tenths = eff[t.uid]
            task_cal = None
            if t.calendar is not None:
                if t.calendar not in named_cal_uid:
                    raise ValueError(f"task {t.uid} references unknown calendar {t.calendar!r}")
                task_cal = named_cal_uid[t.calendar]
            emit(self.proto["task"], t.uid, tid, t.name, start, finish, dur_tenths,
                 t.outline_level, t.parent_uid, t.guid, parent_guid, t.uid in children, pos,
                 t.duration_units, t.estimated, wsum[t.uid], task_cal, t, pct_eff[t.uid])
            pos += 1

        # assemble streams: FixedMeta offset field (bytes 4..8) = record offset in FixedData
        def assemble(recs, metas):
            data = bytearray(); off = 0
            for r, m in zip(recs, metas):
                struct.pack_into("<I", m, 4, off)
                data += r; off += len(r)
            return bytes(data), [bytes(m) for m in metas]

        fd, fm = assemble(fixed, meta)
        fd2, fm2 = assemble(fixed2, meta2)
        t = f"{PRJ}/TBkndTask"
        self._set(f"{t}/FixedData", fd)
        self._set(f"{t}/FixedMeta", B.build_fixed_meta(self.task_meta_hdr, fm, len(fd)))
        self._set(f"{t}/Fixed2Data", fd2)
        self._set(f"{t}/Fixed2Meta", B.build_fixed_meta(self.task_meta2_hdr, fm2, len(fd2)))
        vm, vd = B.build_var_blocks(self.task_var_hdr, var_entries)
        self._set(f"{t}/VarMeta", vm)
        self._set(f"{t}/Var2Data", vd)

        # relations — always rebuilt, so the template's own links never leak through
        if project.relations and self.rel_proto is None:
            raise ValueError("template has no dependency records to use as a prototype; "
                             "save the template with at least one linked pair of tasks")
        rfixed, rfixed2, rmeta, rmeta2 = [], [], [], []
        for i, r in enumerate(project.relations, start=1):
            rec = bytearray(self.rel_proto["rec"]); rec2 = bytearray(self.rel_proto["rec2"])
            struct.pack_into("<III", rec, 0, i, r.pred_uid, r.succ_uid)
            struct.pack_into("<H", rec, 12, REL_TYPES[r.type])
            lag = int(round(r.lag_days * TENTHS_PER_DAY))
            if self.rel_2010_layout:
                struct.pack_into("<Hi", rec, 14, 7, lag)        # lag units (days), lag
            else:
                struct.pack_into("<iH", rec, 14, lag, 7)        # lag, lag units (days)
            rec2[0:16] = uuid.uuid4().bytes_le
            rec2[16:32] = by_uid[r.pred_uid].guid
            rec2[32:48] = by_uid[r.succ_uid].guid
            rfixed.append(bytes(rec)); rfixed2.append(bytes(rec2))
            rmeta.append(bytearray(self.rel_proto["meta"])); rmeta2.append(bytearray(self.rel_proto["meta2"]))
        rfd, rfm = assemble(rfixed, rmeta)
        rfd2, rfm2 = assemble(rfixed2, rmeta2)
        c = f"{PRJ}/TBkndCons"
        self._set(f"{c}/FixedData", rfd)
        self._set(f"{c}/FixedMeta", B.build_fixed_meta(self.rel_meta_hdr, rfm, len(rfd)))
        self._set(f"{c}/Fixed2Data", rfd2)
        self._set(f"{c}/Fixed2Meta", B.build_fixed_meta(self.rel_meta2_hdr, rfm2, len(rfd2)))

        # resources ------------------------------------------------------------
        rsc_count = None
        if project.resources:
            if self.rsc_proto is None:
                raise ValueError("template has no uid-0 resource record to use as a prototype")
            uids = [r.uid for r in project.resources]
            if len(set(uids)) != len(uids) or any(u <= 0 for u in uids):
                raise ValueError("resource uids must be unique and > 0")
            rrows = [r for r in self.rsc_rows]           # stubs + uid 0, kept verbatim
            rvar_entries = [(struct.unpack_from("<I", row["rec"], 0)[0], typ, payload)
                            for row in self.rsc_rows if len(row["rec"]) > 16
                            for typ, payload in row["var"]]
            for idx, res in enumerate(project.resources, start=1):
                rec = bytearray(self.rsc_proto["rec"]); rec2 = bytearray(self.rsc_proto["rec2"])
                m = bytearray(self.rsc_proto["meta"]); m2 = bytearray(self.rsc_proto["meta2"])
                # meta byte 2 counts the record's var entries
                m[2] = len(self.rsc_proto["var"]) + 1 + bool(res.initials) + bool(res.email)
                cal_uid = next_cal_uid
                next_cal_uid += 1
                self._putf(self.rsc_fm, RSC_NATIVE, rec, rec2, "UNIQUE_ID", "<I", res.uid)
                self._putf(self.rsc_fm, RSC_NATIVE, rec, rec2, "ID", "<I", idx)
                self._putf(self.rsc_fm, RSC_NATIVE, rec, rec2, "MAX_UNITS", "<d", res.max_units * PCT_SCALE)
                self._putf(self.rsc_fm, RSC_NATIVE, rec, rec2, "CALENDAR_UID", "<i", cal_uid)
                self._putf(self.rsc_fm, RSC_NATIVE, rec, rec2, "POSITION", "<d", float(idx + 1))
                self._putf_bytes(self.rsc_fm, RSC_NATIVE, rec, rec2, "GUID", res.guid)
                self._putf_bytes(self.rsc_fm, RSC_NATIVE, rec, rec2, "CALENDAR_GUID", res.guid)
                for name in ("UNIQUE_ID", "ID", "NAME", "MAX_UNITS"):
                    self._bitf(self.rsc_bit, RSC_NATIVE, m, m2, name, True)
                self._bitf(self.rsc_bit, RSC_NATIVE, m, m2, "INITIALS", bool(res.initials))
                self._bitf(self.rsc_bit, RSC_NATIVE, m, m2, "EMAIL_ADDRESS", bool(res.email))
                rrows.append(dict(rec=bytes(rec), rec2=bytes(rec2), meta=m, meta2=m2))
                for typ, payload in self.rsc_proto["var"]:
                    rvar_entries.append((res.uid, typ, payload))
                rvar_entries.append((res.uid, RSC_NATIVE["NAME"], B.encode_unicode(res.name)))
                if res.initials:
                    rvar_entries.append((res.uid, RSC_NATIVE["INITIALS"], B.encode_unicode(res.initials)))
                if res.email:
                    rvar_entries.append((res.uid, RSC_NATIVE["EMAIL_ADDRESS"], B.encode_unicode(res.email)))
                # per-resource calendar: (cal uid, base = Standard, resource uid),
                # calendar GUID = resource GUID, third GUID = Standard calendar's
                if self.cal_proto is not None:
                    crec = bytearray(self.cal_proto["rec"])
                    uc, bc, rc = self.cal_cols
                    struct.pack_into("<i", crec, uc * 4, cal_uid)
                    struct.pack_into("<i", crec, bc * 4, self.cal_standard_uid)
                    struct.pack_into("<i", crec, rc * 4, res.uid)
                    crec2 = bytearray(self.cal_proto["rec2"])
                    crec2[0:16] = res.guid
                    crec2[16:32] = res.guid
                    crec2[32:48] = self.cal_standard_guid
                    cal_rows_out.append(dict(rec=bytes(crec), rec2=bytes(crec2),
                                             meta=bytearray(self.cal_proto["meta"]),
                                             meta2=bytearray(self.cal_proto["meta2"])))
            rfd, rfm = assemble([r["rec"] for r in rrows], [bytearray(r["meta"]) for r in rrows])
            rfd2, rfm2 = assemble([r["rec2"] for r in rrows], [bytearray(r["meta2"]) for r in rrows])
            self._set(f"{PRJ}/TBkndRsc/FixedData", rfd)
            self._set(f"{PRJ}/TBkndRsc/FixedMeta", B.build_fixed_meta(self.rsc_meta_hdr, rfm, len(rfd)))
            self._set(f"{PRJ}/TBkndRsc/Fixed2Data", rfd2)
            self._set(f"{PRJ}/TBkndRsc/Fixed2Meta", B.build_fixed_meta(self.rsc_meta2_hdr, rfm2, len(rfd2)))
            rvm, rvd = B.build_var_blocks(self.rsc_var_hdr, rvar_entries, B.RESOURCE_FIELD_HI)
            self._set(f"{PRJ}/TBkndRsc/VarMeta", rvm)
            self._set(f"{PRJ}/TBkndRsc/Var2Data", rvd)
            rsc_count = len(rrows)

        # TBkndCal: fixed streams rewritten when rows were added (new base or
        # resource calendars) or metas patched; var streams when names/data
        # blobs were added
        if len(cal_rows_out) != len(self.cal_rows) or cal_meta_patched:
            cfd, cfm = assemble([c["rec"] for c in cal_rows_out], [bytearray(c["meta"]) for c in cal_rows_out])
            cfd2, cfm2 = assemble([c["rec2"] for c in cal_rows_out], [bytearray(c["meta2"]) for c in cal_rows_out])
            self._set(f"{PRJ}/TBkndCal/FixedData", cfd)
            self._set(f"{PRJ}/TBkndCal/FixedMeta", B.build_fixed_meta(self.cal_meta_hdr, cfm, len(cfd)))
            self._set(f"{PRJ}/TBkndCal/Fixed2Data", cfd2)
            self._set(f"{PRJ}/TBkndCal/Fixed2Meta", B.build_fixed_meta(self.cal_meta2_hdr, cfm2, len(cfd2)))
        if cal_var_new:
            cvm, cvd = B.build_var_blocks(self.cal_var_hdr, self.cal_var_entries + cal_var_new,
                                          self.cal_var_hi)
            self._set(f"{PRJ}/TBkndCal/VarMeta", cvm)
            self._set(f"{PRJ}/TBkndCal/Var2Data", cvd)
            # Project reads base-calendar data blobs only when this Props count
            # says edited base calendars exist (resource-calendar blobs load
            # regardless of it)
            n_edited = sum(1 for uid, typ, _ in cal_var_new if typ == CAL_DATA_VAR)
            if B.PROPS_EDITED_BASE_CALENDARS in self.props:
                self.props[B.PROPS_EDITED_BASE_CALENDARS] = struct.pack("<I", n_edited)

        # assignments ----------------------------------------------------------
        # the template's phantom per-task records are never kept: Project joins them
        # to tasks by unique id and overrides the task's duration from them
        rsc_by_uid = {r.uid: r for r in project.resources}
        if project.assignments and self.assn_proto is None:
            raise ValueError("template has no assignment records to use as a prototype")
        afixed, afixed2, ameta, ameta2, avar_entries = [], [], [], [], []
        for i, asn in enumerate(project.assignments, start=1):
            if asn.task_uid not in by_uid:
                raise ValueError(f"assignment references unknown task uid {asn.task_uid}")
            if asn.resource_uid not in rsc_by_uid:
                raise ValueError(f"assignment references unknown resource uid {asn.resource_uid}")
            task, res = by_uid[asn.task_uid], rsc_by_uid[asn.resource_uid]
            start, finish, dur_tenths = eff[asn.task_uid]
            rec = bytearray(self.assn_proto["rec"]); rec2 = bytearray(self.assn_proto["rec2"])
            m = bytearray(self.assn_proto["meta"]); m2 = bytearray(self.assn_proto["meta2"])
            put = lambda name, fmt, v: self._putf(self.assn_fm, ASSN_NATIVE, rec, rec2, name, fmt, v)
            put("UNIQUE_ID", "<I", i)
            put("TASK_UNIQUE_ID", "<I", asn.task_uid)
            put("RESOURCE_UNIQUE_ID", "<i", asn.resource_uid)
            put("UNITS", "<d", asn.units * PCT_SCALE)
            work = dur_tenths * WORK_SCALE * asn.units
            for name in ("WORK", "REGULAR_WORK", "REMAINING_WORK"):
                put(name, "<d", work)
            tpct = pct_eff.get(asn.task_uid, 0)
            reached = start
            if tpct:
                put("ACTUAL_WORK", "<d", work * tpct / 100.0)
                put("REMAINING_WORK", "<d", work * (100 - tpct) / 100.0)
                # how far work has got: Project reconciles the task's actuals
                # against this, and a stop still at the start knocked a
                # 100%-complete task back to 99% with a zero duration
                reached = (finish if tpct == 100 else
                           advance_working(start, int(round(dur_tenths * tpct / 100)), pattern))
            self._putf_ts(self.assn_fm, ASSN_NATIVE, rec, rec2, "START", start)
            for name in ("RESUME", "STOP"):
                self._putf_ts(self.assn_fm, ASSN_NATIVE, rec, rec2, name, reached)
            self._putf_ts(self.assn_fm, ASSN_NATIVE, rec, rec2, "FINISH", finish)
            self._putf_bytes(self.assn_fm, ASSN_NATIVE, rec, rec2, "GUID", uuid.uuid4().bytes_le)
            self._putf_bytes(self.assn_fm, ASSN_NATIVE, rec, rec2, "TASK_GUID", task.guid)
            self._putf_bytes(self.assn_fm, ASSN_NATIVE, rec, rec2, "RESOURCE_GUID", res.guid)
            for name in ("UNIQUE_ID", "TASK_UNIQUE_ID", "RESOURCE_UNIQUE_ID", "UNITS", "WORK"):
                self._bitf(self.assn_bit, ASSN_NATIVE, m, m2, name, True)
            afixed.append(bytes(rec)); afixed2.append(bytes(rec2))
            ameta.append(m); ameta2.append(m2)
            for typ, payload in self.assn_proto["var"]:
                if typ == ASSN_NATIVE["CREATED"]:
                    payload = B.encode_timestamp(datetime.now().replace(second=0, microsecond=0))
                elif typ == ASSN_NATIVE["PLANNED_WORK_DATA"] and len(payload) >= 36:
                    # planned-work contour: Project schedules the assignment from this
                    # blob, not from the fixed WORK field (which MPXJ reads).
                    # +8 double: units * 16 (80000.0 at 50% in a Project-saved file),
                    # +16 double: total work (milli-minutes),
                    # +24 uint32: elapsed assignment duration in tenths * 8 —
                    # writing work*0.08 here made a 50% assignment display half its
                    # real duration (the two only coincide at 100% units)
                    b2 = bytearray(payload)
                    struct.pack_into("<d", b2, 8, asn.units * PCT_SCALE * 16)
                    struct.pack_into("<d", b2, 16, work)
                    struct.pack_into("<I", b2, 24, dur_tenths * 8)
                    payload = bytes(b2)
                avar_entries.append((i, typ, payload))
        a = f"{PRJ}/TBkndAssn"
        afd, afm = assemble(afixed, ameta)
        afd2, afm2 = assemble(afixed2, ameta2)
        self._set(f"{a}/FixedData", afd)
        self._set(f"{a}/FixedMeta", B.build_fixed_meta(self.assn_meta_hdr, afm, len(afd)))
        self._set(f"{a}/Fixed2Data", afd2)
        self._set(f"{a}/Fixed2Meta", B.build_fixed_meta(self.assn_meta2_hdr, afm2, len(afd2)))
        avm, avd = B.build_var_blocks(self.assn_var_hdr, avar_entries, B.ASSIGNMENT_FIELD_HI)
        self._set(f"{a}/VarMeta", avm)
        self._set(f"{a}/Var2Data", avd)

        # record-count dwords: Project sizes its tables from these and drops records
        # beyond the count
        counters = [(B.PROPS_TASK_RECORD_COUNT, len(fixed)),
                    (B.PROPS_ASSN_RECORD_COUNT, len(project.assignments)),
                    (B.PROPS_REL_RECORD_COUNT, len(project.relations))]
        if rsc_count is not None:
            counters += [(B.PROPS_RESOURCE_RECORD_COUNT, rsc_count),
                         (B.PROPS_RESOURCE_RECORD_COUNT + 1, rsc_count + 2)]   # 0x1000003, see FORMAT_NOTES
        for key, n in counters:
            if key in self.props:
                self.props[key] = struct.pack("<I", n)

        # per-storage Var2Data lengths: Project truncates its var-data read at
        # the declared size, so stale values hide names and calendar data
        for storage, key in B.PROPS_VAR2DATA_SIZE.items():
            if key in self.props:
                try:
                    self.props[key] = struct.pack("<I", len(self._get(f"{PRJ}/{storage}/Var2Data")))
                except KeyError:
                    pass

        # project properties: dates, title, default calendar, currency
        if project.default_calendar is not None:
            if project.default_calendar not in named_cal_uid:
                raise ValueError(f"unknown default calendar {project.default_calendar!r}")
            if B.PROPS_DEFAULT_CALENDAR_NAME in self.props:
                self.props[B.PROPS_DEFAULT_CALENDAR_NAME] = \
                    project.default_calendar.encode("utf-16-le") + b"\0" * 4
        self.props[B.PROPS_PROJECT_START_DATE] = B.encode_timestamp(project.start)
        if B.PROPS_PROJECT_FINISH_DATE in self.props:
            self.props[B.PROPS_PROJECT_FINISH_DATE] = B.encode_timestamp(p_finish)
        if project.status_date is not None and B.PROPS_STATUS_DATE in self.props:
            self.props[B.PROPS_STATUS_DATE] = B.encode_timestamp(project.status_date)
        if project.currency_symbol is not None and B.PROPS_CURRENCY_SYMBOL in self.props:
            self.props[B.PROPS_CURRENCY_SYMBOL] = \
                project.currency_symbol.encode("utf-16-le") + b"\0\0"
        if project.currency_code is not None and B.PROPS_CURRENCY_CODE in self.props:
            self.props[B.PROPS_CURRENCY_CODE] = \
                project.currency_code.encode("utf-16-le") + b"\0\0"
        # stale 2010-era next-uid counters make Project renumber task uids;
        # M365 itself deletes the key on save
        if B.PROPS_LEGACY_NEXT_UIDS in self.props:
            del self.props[B.PROPS_LEGACY_NEXT_UIDS]
            self.props_order.remove(B.PROPS_LEGACY_NEXT_UIDS)
        if B.PROPS_TITLE in self.props:
            self.props[B.PROPS_TITLE] = project.title.encode("utf-16-le") + b"\0" * 4   # Props strings: double NUL
        self._set(f"{PRJ}/Props", B.build_props(self.props_hdr, self.props, self.props_order))

        # Gantt scroll position: the view state (CV_iew) stores the visible date
        # as a timestamp equal to the template's project start — retarget it so
        # the chart opens on this schedule instead of the template's save date
        new_start = B.encode_timestamp(project.start)
        if len(self.template_start) == 4 and self.template_start != new_start:
            try:
                vd = self._get("   214/CV_iew/Var2Data")
                if self.template_start in vd:
                    self._set("   214/CV_iew/Var2Data",
                              vd.replace(self.template_start, new_start))
            except KeyError:
                pass

        # document metadata: SummaryInformation (title, subject, author, keywords,
        # comments) + DocumentSummaryInformation (manager, company, category)
        si_updates = {2: project.title}
        for pid, val in ((3, project.subject), (4, project.author),
                         (5, project.keywords), (6, project.comments)):
            if val is not None:
                si_updates[pid] = val
        try:
            si = self.root.children["\x05SummaryInformation"]
            self.root.set_path("\x05SummaryInformation",
                               B.update_property_set_strings(si, si_updates))
            dsi_updates = {}
            for pid, val in ((14, project.manager), (15, project.company),
                             (2, project.category)):
                if val is not None:
                    dsi_updates[pid] = val
            if dsi_updates:
                dsi = self.root.children["\x05DocumentSummaryInformation"]
                self.root.set_path("\x05DocumentSummaryInformation",
                                   B.update_property_set_strings(dsi, dsi_updates))
        except KeyError:
            pass
        return write_cfb(self.root, root_clsid=PROJECT_CLSID)

    def write(self, project: Project, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.build(project))
