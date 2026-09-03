"""Template-based MPP14 writer.

Strategy: start from a minimal .mpp saved by Microsoft Project (the template),
keep every stream we don't understand untouched, and regenerate only the
task / dependency data streams by cloning prototype records from the template
and patching the fields we control.
"""
from __future__ import annotations
import struct
import uuid
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
          "TASK_MODE": 1280, "WORK": 0, "REMAINING_WORK": 4, "CALENDAR_UNIQUE_ID": 401}
CAL_NAME_VAR, CAL_DATA_VAR = 1, 8
RSC_NATIVE = {"UNIQUE_ID": 27, "ID": 0, "NAME": 1, "INITIALS": 2, "EMAIL_ADDRESS": 35,
              "MAX_UNITS": 4, "CALENDAR_UID": 56, "GUID": 728, "CALENDAR_GUID": 729,
              "POSITION": 730}
ASSN_NATIVE = {"UNIQUE_ID": 0, "TASK_UNIQUE_ID": 1, "RESOURCE_UNIQUE_ID": 2, "START": 20,
               "FINISH": 21, "RESUME": 24, "STOP": 264, "UNITS": 7, "WORK": 8,
               "REGULAR_WORK": 11, "REMAINING_WORK": 12, "GUID": 636, "TASK_GUID": 637,
               "RESOURCE_GUID": 638, "CREATED": 634, "PLANNED_WORK_DATA": 49}
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


class MppWriter:
    def __init__(self, template_path: str):
        self.root = load_cfb(template_path)
        self.prj = self.root.storage_path(PRJ)
        hdr, props, order = B.parse_props(self._get(f"{PRJ}/Props"))
        self.props_hdr, self.props, self.props_order = hdr, props, order
        self.task_fm, self.task_bit = self._parse_class_map(B.PROPS_TASK_FIELD_MAP)
        self.rsc_fm, self.rsc_bit = self._parse_class_map(B.PROPS_RESOURCE_FIELD_MAP)
        self.assn_fm, self.assn_bit = self._parse_class_map(B.PROPS_ASSIGNMENT_FIELD_MAP)
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

    def _load_prototypes(self) -> None:
        t = f"{PRJ}/TBkndTask"
        mh, _, mitems = B.parse_fixed_meta(self._get(f"{t}/FixedMeta"), TASK_META_SIZE)
        recs = B.split_fixed_data(self._get(f"{t}/FixedData"), mitems)
        m2h, _, m2items = B.parse_fixed_meta(self._get(f"{t}/Fixed2Meta"), TASK_META2_SIZE)
        recs2 = B.split_fixed_data(self._get(f"{t}/Fixed2Data"), m2items)
        vh, vtable, _ = B.parse_var_meta(self._get(f"{t}/VarMeta"))
        vdata = self._get(f"{t}/Var2Data")
        self.task_meta_hdr, self.task_meta2_hdr, self.task_var_hdr = mh, m2h, vh
        # prototype = first full-size record that is a real task (uid > 0) else summary
        full = [i for i, r in enumerate(recs) if len(r) > 100]
        if not full:
            raise ValueError("template has no task records to use as prototypes")
        summary_i = full[0]
        task_i = full[1] if len(full) > 1 else full[0]
        self.proto = {}
        for label, i in (("summary", summary_i), ("task", task_i)):
            uid = struct.unpack_from("<I", recs[i], 0)[0]
            var = [(typ, B.read_var(vdata, off)) for typ, off in sorted(vtable.get(uid, {}).items())]
            self.proto[label] = dict(rec=recs[i], rec2=recs2[i], meta=mitems[i], meta2=m2items[i], var=var)
        # deleted/null-task stubs at the front of the block (kept verbatim)
        self.stubs = [(recs[i], recs2[i], mitems[i], m2items[i]) for i in range(len(recs)) if len(recs[i]) <= 16]
        # relations
        c = f"{PRJ}/TBkndCons"
        rmh, _, rmitems = B.parse_fixed_meta(self._get(f"{c}/FixedMeta"), REL_META_SIZE)
        rrecs = B.split_fixed_data(self._get(f"{c}/FixedData"), rmitems)
        rm2h, _, rm2items = B.parse_fixed_meta(self._get(f"{c}/Fixed2Meta"), REL_META2_SIZE)
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
        amd = self._get(f"{a}/FixedMeta")
        amh, _, amitems = B.parse_fixed_meta(amd, ASSN_META_SIZE)
        arecs = B.split_fixed_data(self._get(f"{a}/FixedData"), amitems)
        am2d = self._get(f"{a}/Fixed2Meta")
        am2n = struct.unpack_from("<I", am2d, 8)[0]
        am2size = (len(am2d) - 16) // am2n if am2n else 0
        am2h, _, am2items = B.parse_fixed_meta(am2d, am2size) if am2n else (am2d[:16], 0, [])
        arecs2 = B.split_fixed_data(self._get(f"{a}/Fixed2Data"), am2items)
        avh, avtable, _ = B.parse_var_meta(self._get(f"{a}/VarMeta"))
        avdata = self._get(f"{a}/Var2Data")
        self.assn_meta_hdr, self.assn_meta2_hdr, self.assn_var_hdr = amh[:16], am2h[:16], avh[:24]
        self.assn_proto = None
        for i, rec in enumerate(arecs):
            if len(rec) > 50 and i < len(arecs2):
                uid = struct.unpack_from("<I", rec, 0)[0]
                var = [(typ, B.read_var(avdata, off)) for typ, off in sorted(avtable.get(uid, {}).items())]
                self.assn_proto = dict(rec=rec, rec2=arecs2[i], meta=amitems[i], meta2=am2items[i], var=var)
                break
        # resources: the uid-0 "Unassigned" system record (present even in a blank
        # project) is the prototype for real resource records
        rs = f"{PRJ}/TBkndRsc"
        rsmh, _, rsmitems = B.parse_fixed_meta(self._get(f"{rs}/FixedMeta"), RSC_META_SIZE)
        rsrecs = B.split_fixed_data(self._get(f"{rs}/FixedData"), rsmitems)
        rsm2d = self._get(f"{rs}/Fixed2Meta")
        rsm2n = struct.unpack_from("<I", rsm2d, 8)[0]
        rsm2size = (len(rsm2d) - 16) // rsm2n if rsm2n else 0
        rsm2h, _, rsm2items = B.parse_fixed_meta(rsm2d, rsm2size) if rsm2n else (rsm2d[:16], 0, [])
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
        clmh, _, clmitems = B.parse_fixed_meta(self._get(f"{cl}/FixedMeta"), CAL_META_SIZE)
        clrecs = B.split_fixed_data(self._get(f"{cl}/FixedData"), clmitems)
        clm2d = self._get(f"{cl}/Fixed2Meta")
        clm2n = struct.unpack_from("<I", clm2d, 8)[0]
        clm2size = (len(clm2d) - 16) // clm2n if clm2n else 0
        clm2h, _, clm2items = B.parse_fixed_meta(clm2d, clm2size) if clm2n else (clm2d[:16], 0, [])
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
        self.cal_base_row = None
        base_row = None
        for row in self.cal_rows:
            if len(row["rec"]) == 12:
                d = struct.unpack("<3i", row["rec"])
                if d.count(-1) == 2:                       # base calendar row (Standard)
                    uid_col = next(j for j in range(3) if d[j] != -1)
                    base_row = row
                    self.cal_standard_uid = d[uid_col]
                    self.cal_standard_guid = row["rec2"][:16]
                    self.cal_uid_col = uid_col
        self.cal_base_row = base_row
        if base_row is not None:
            for row in self.cal_rows:
                if len(row["rec"]) == 12 and row is not base_row:
                    d = struct.unpack("<3i", row["rec"])
                    others = [j for j in range(3) if j != self.cal_uid_col]
                    base_col = next((j for j in others if d[j] == self.cal_standard_uid), others[0])
                    rsc_col = next(j for j in others if j != base_col)
                    self.cal_cols = (self.cal_uid_col, base_col, rsc_col)
                    self.cal_proto = row
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
                    m[8] |= 0x80
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
                m[8] |= 0x80
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
                 cal_uid: Optional[int] = None):
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
            rec2[0:16] = guid                 # task GUID (field map block 1, offset 0)
            struct.pack_into("<d", rec2, 16, float(position))
            rec2[24:40] = parent_guid         # parent task GUID
            # boolean task fields are bits in the FixedMeta/Fixed2Meta bitmap,
            # one bit per field-map entry, indexed by entry position
            m = bytearray(proto["meta"]); m2 = bytearray(proto["meta2"])
            self._put_bit(m, m2, "SUMMARY", is_summary)
            self._put_bit(m, m2, "MILESTONE", not is_summary and dur_tenths == 0)
            self._put_bit(m, m2, "ESTIMATED", estimated)
            if cal_uid is not None:
                self._put(rec, "CALENDAR_UNIQUE_ID", "<i", cal_uid)
                self._put_bit(m, m2, "CALENDAR_UNIQUE_ID", True)
            fixed.append(bytes(rec)); fixed2.append(bytes(rec2))
            meta.append(m); meta2.append(m2)
            for typ, payload in proto["var"]:
                if typ == NATIVE["NAME"]:
                    payload = B.encode_unicode(name)
                var_entries.append((uid, typ, payload))

        emit(self.proto["summary"], 0, 0, project.title, p_start, p_finish,
             working_tenths(p_start, p_finish, pattern), 0, 0, summary_guid, b"\0" * 16, True, 1,
             work=sum(wsum[t.uid] for t in project.tasks if t.parent_uid == 0))
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
                 t.duration_units, t.estimated, wsum[t.uid], task_cal)
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
            struct.pack_into("<HH", rec, 12, REL_TYPES[r.type], 7)  # lag units: days
            struct.pack_into("<i", rec, 16, int(round(r.lag_days * TENTHS_PER_DAY)))
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
            for name in ("START", "RESUME", "STOP"):
                self._putf_ts(self.assn_fm, ASSN_NATIVE, rec, rec2, name, start)
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

        # project properties: start date + title + default calendar
        if project.default_calendar is not None:
            if project.default_calendar not in named_cal_uid:
                raise ValueError(f"unknown default calendar {project.default_calendar!r}")
            if B.PROPS_DEFAULT_CALENDAR_NAME in self.props:
                self.props[B.PROPS_DEFAULT_CALENDAR_NAME] = \
                    project.default_calendar.encode("utf-16-le") + b"\0" * 4
        self.props[B.PROPS_PROJECT_START_DATE] = B.encode_timestamp(project.start)
        if B.PROPS_TITLE in self.props:
            self.props[B.PROPS_TITLE] = project.title.encode("utf-16-le") + b"\0" * 4   # Props strings: double NUL
        self._set(f"{PRJ}/Props", B.build_props(self.props_hdr, self.props, self.props_order))
        return write_cfb(self.root, root_clsid=PROJECT_CLSID)

    def write(self, project: Project, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.build(project))
