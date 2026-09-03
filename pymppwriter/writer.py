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
TENTHS_PER_DAY = 4800          # 8h * 60m * 10
NATIVE = {"UNIQUE_ID": 86, "ID": 23, "NAME": 14, "START": 35, "FINISH": 36, "DURATION": 29,
          "REMAINING_DURATION": 31, "OUTLINE_LEVEL": 249, "PARENT_UID": 160, "EARLY_START": 37,
          "EARLY_FINISH": 38, "LATE_START": 39, "LATE_FINISH": 40, "CREATED": 93, "GUID": 1143,
          "MILESTONE": 24, "SUMMARY": 92, "ESTIMATED": 396, "ACTUAL_DURATION_UNITS": 181,
          "TASK_MODE": 1280}
REL_TYPES = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}
UNITS_CODES = {"m": 3, "h": 5, "d": 7, "w": 9, "mo": 11}
SUMMARY_UNITS = 0x15           # what Project writes in the units word of summary rows
ESTIMATED_FLAG = 0x20          # OR'ed into the units word; shows as "3 days?"
WORK_WINDOWS = ((8 * 60, 12 * 60), (13 * 60, 17 * 60))   # Standard calendar, minutes from midnight


def working_tenths(start: datetime, finish: datetime) -> int:
    """Working time between two datetimes in tenths of a minute, using the
    Standard calendar (Mon-Fri, 08:00-12:00 and 13:00-17:00)."""
    if finish <= start:
        return 0
    total, day = 0, start.date()
    while day <= finish.date():
        if day.weekday() < 5:
            for w0, w1 in WORK_WINDOWS:
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
    guid: bytes = field(default_factory=lambda: uuid.uuid4().bytes_le)


@dataclass
class Relation:
    pred_uid: int
    succ_uid: int
    type: str = "FS"
    lag_days: float = 0.0


@dataclass
class Project:
    title: str
    start: datetime
    tasks: List[Task] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)


class MppWriter:
    def __init__(self, template_path: str):
        self.root = load_cfb(template_path)
        self.prj = self.root.storage_path(PRJ)
        hdr, props, order = B.parse_props(self._get(f"{PRJ}/Props"))
        self.props_hdr, self.props, self.props_order = hdr, props, order
        self.task_fm = {}
        self.task_bit = {}    # native id -> field map entry index = bit index in FixedMeta/Fixed2Meta
        for i, it in enumerate(B.parse_field_map(props[B.PROPS_TASK_FIELD_MAP])):
            if it.in_fixed:
                self.task_fm.setdefault(it.type_value & 0xFFFF, it)
            self.task_bit.setdefault(it.type_value & 0xFFFF, i)
        self._load_prototypes()

    # ------------------------------------------------------------ helpers --
    def _get(self, path: str) -> bytes:
        s = self.root
        parts = path.split("/")
        for p in parts[:-1]:
            s = s.children[p]
        return s.children[parts[-1]]

    def _set(self, path: str, data: bytes) -> None:
        self.root.set_path(path, data)

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
        # assignments: only the stream headers — the template's phantom per-task
        # assignment records are cleared on write (Project overrides task duration
        # with assignment data joined by task UID)
        a = f"{PRJ}/TBkndAssn"
        self.assn_meta_hdr = self._get(f"{a}/FixedMeta")[:16]
        self.assn_meta2_hdr = self._get(f"{a}/Fixed2Meta")[:16]
        self.assn_var_hdr = self._get(f"{a}/VarMeta")[:24]

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

        eff: Dict[int, tuple] = {}    # uid -> (start, finish, dur_tenths)
        for t in sorted(project.tasks, key=depth, reverse=True):
            kids = children.get(t.uid)
            if kids:
                s = min(eff[k.uid][0] for k in kids)
                f = max(eff[k.uid][1] for k in kids)
                eff[t.uid] = (s, f, working_tenths(s, f))
            else:
                eff[t.uid] = (t.start, t.finish, int(round(t.duration_days * TENTHS_PER_DAY)))

        # project summary task (uid 0) spans all tasks
        p_start = min([eff[t.uid][0] for t in project.tasks] or [project.start])
        p_finish = max([eff[t.uid][1] for t in project.tasks] or [project.start])
        summary_guid = uuid.uuid4().bytes_le

        fixed, fixed2, meta, meta2, var_entries = [], [], [], [], []
        for s in self.stubs:
            fixed.append(s[0]); fixed2.append(s[1]); meta.append(bytearray(s[2])); meta2.append(bytearray(s[3]))

        def emit(proto: dict, uid: int, tid: int, name: str, start, finish, dur_tenths: int,
                 level: int, parent_uid: int, guid: bytes, parent_guid: bytes, is_summary: bool,
                 position: int, units: str = "d", estimated: bool = False):
            rec = bytearray(proto["rec"]); rec2 = bytearray(proto["rec2"])
            self._put(rec, "UNIQUE_ID", "<I", uid)
            self._put(rec, "ID", "<I", tid)
            self._put(rec, "OUTLINE_LEVEL", "<H", level)
            self._put(rec, "PARENT_UID", "<I", parent_uid)
            self._put(rec, "DURATION", "<i", dur_tenths)
            self._put(rec, "REMAINING_DURATION", "<i", dur_tenths)
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
            fixed.append(bytes(rec)); fixed2.append(bytes(rec2))
            meta.append(m); meta2.append(m2)
            for typ, payload in proto["var"]:
                if typ == NATIVE["NAME"]:
                    payload = B.encode_unicode(name)
                var_entries.append((uid, typ, payload))

        emit(self.proto["summary"], 0, 0, project.title, p_start, p_finish,
             working_tenths(p_start, p_finish), 0, 0, summary_guid, b"\0" * 16, True, 1)
        pos = 2
        # tasks in ID (display) order = list order
        for tid, t in enumerate(project.tasks, start=1):
            parent_guid = summary_guid if t.parent_uid == 0 else by_uid[t.parent_uid].guid
            start, finish, dur_tenths = eff[t.uid]
            emit(self.proto["task"], t.uid, tid, t.name, start, finish, dur_tenths,
                 t.outline_level, t.parent_uid, t.guid, parent_guid, t.uid in children, pos,
                 t.duration_units, t.estimated)
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

        # assignments: clear the template's phantom per-task records — Project joins
        # them to tasks by unique id and overrides the task's duration from them
        a = f"{PRJ}/TBkndAssn"
        self._set(f"{a}/FixedData", b"")
        self._set(f"{a}/FixedMeta", B.build_fixed_meta(self.assn_meta_hdr, [], 0))
        self._set(f"{a}/Fixed2Data", b"")
        self._set(f"{a}/Fixed2Meta", B.build_fixed_meta(self.assn_meta2_hdr, [], 0))
        avm = bytearray(self.assn_var_hdr)
        struct.pack_into("<I", avm, 8, 0)     # entry count
        struct.pack_into("<I", avm, 20, 0)    # Var2Data size
        self._set(f"{a}/VarMeta", bytes(avm))
        self._set(f"{a}/Var2Data", b"")

        # record-count dwords: Project sizes its tables from these and drops records
        # beyond the count
        for key, n in ((B.PROPS_TASK_RECORD_COUNT, len(fixed)),
                       (B.PROPS_ASSN_RECORD_COUNT, 0),
                       (B.PROPS_REL_RECORD_COUNT, len(project.relations))):
            if key in self.props:
                self.props[key] = struct.pack("<I", n)

        # project properties: start date + title
        self.props[B.PROPS_PROJECT_START_DATE] = B.encode_timestamp(project.start)
        if B.PROPS_TITLE in self.props:
            self.props[B.PROPS_TITLE] = project.title.encode("utf-16-le") + b"\0" * 4   # Props strings: double NUL
        self._set(f"{PRJ}/Props", B.build_props(self.props_hdr, self.props, self.props_order))
        return write_cfb(self.root, root_clsid=PROJECT_CLSID)

    def write(self, project: Project, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.build(project))
