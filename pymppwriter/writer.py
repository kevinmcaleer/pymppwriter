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
REL_META_SIZE, REL_META2_SIZE = 10, 10
TENTHS_PER_DAY = 4800          # 8h * 60m * 10
NATIVE = {"UNIQUE_ID": 86, "ID": 23, "NAME": 14, "START": 35, "FINISH": 36, "DURATION": 29,
          "REMAINING_DURATION": 31, "OUTLINE_LEVEL": 249, "PARENT_UID": 160, "EARLY_START": 37,
          "EARLY_FINISH": 38, "LATE_START": 39, "LATE_FINISH": 40, "CREATED": 93, "GUID": 1143,
          "MILESTONE": 24, "SUMMARY": 92}
REL_TYPES = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}


@dataclass
class Task:
    uid: int
    name: str
    start: datetime
    finish: datetime
    duration_days: float = 1.0
    outline_level: int = 1
    parent_uid: int = 0            # 0 = project summary task
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
        for it in B.parse_field_map(props[B.PROPS_TASK_FIELD_MAP]):
            if it.in_fixed:
                self.task_fm[it.type_value & 0xFFFF] = it
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

    def _put(self, rec: bytearray, name: str, fmt: str, value) -> None:
        it = self.task_fm.get(NATIVE[name])
        if it is None or it.block != 0:
            return
        struct.pack_into(fmt, rec, it.offset, value)

    def _put_ts(self, rec: bytearray, name: str, dt: Optional[datetime]) -> None:
        it = self.task_fm.get(NATIVE[name])
        if it is not None and it.block == 0:
            rec[it.offset:it.offset + 4] = B.encode_timestamp(dt)

    # ------------------------------------------------------------- build ---
    def build(self, project: Project) -> bytes:
        by_uid: Dict[int, Task] = {t.uid: t for t in project.tasks}
        children: Dict[int, List[Task]] = {}
        for t in project.tasks:
            children.setdefault(t.parent_uid, []).append(t)

        # project summary task (uid 0) spans all tasks
        p_start = min([t.start for t in project.tasks] or [project.start])
        p_finish = max([t.finish for t in project.tasks] or [project.start])
        summary_guid = uuid.uuid4().bytes_le

        fixed, fixed2, meta, meta2, var_entries = [], [], [], [], []
        for s in self.stubs:
            fixed.append(s[0]); fixed2.append(s[1]); meta.append(bytearray(s[2])); meta2.append(bytearray(s[3]))

        def emit(proto: dict, uid: int, tid: int, name: str, start, finish, dur_tenths: int,
                 level: int, parent_uid: int, guid: bytes, parent_guid: bytes, is_summary: bool, position: int):
            rec = bytearray(proto["rec"]); rec2 = bytearray(proto["rec2"])
            self._put(rec, "UNIQUE_ID", "<I", uid)
            self._put(rec, "ID", "<I", tid)
            self._put(rec, "OUTLINE_LEVEL", "<H", level)
            self._put(rec, "PARENT_UID", "<I", parent_uid)
            self._put(rec, "DURATION", "<i", dur_tenths)
            self._put(rec, "REMAINING_DURATION", "<i", dur_tenths)
            for f in ("START", "EARLY_START", "LATE_START"):
                self._put_ts(rec, f, start)
            for f in ("FINISH", "EARLY_FINISH", "LATE_FINISH"):
                self._put_ts(rec, f, finish)
            self._put_ts(rec, "CREATED", datetime.now().replace(second=0, microsecond=0))
            rec2[0:16] = guid                 # task GUID (field map block 1, offset 0)
            struct.pack_into("<d", rec2, 16, float(position))
            rec2[24:40] = parent_guid         # parent task GUID
            # summary flag lives in Fixed2Meta bit field (mask 0x20 in the 3rd dword)
            m2 = bytearray(proto["meta2"])
            if is_summary:
                m2[8] |= 0x20
            else:
                m2[8] &= ~0x20 & 0xFF
            fixed.append(bytes(rec)); fixed2.append(bytes(rec2))
            meta.append(bytearray(proto["meta"])); meta2.append(m2)
            for typ, payload in proto["var"]:
                if typ == NATIVE["NAME"]:
                    payload = B.encode_unicode(name)
                var_entries.append((uid, typ, payload))

        emit(self.proto["summary"], 0, 0, project.title, p_start, p_finish,
             int(round((p_finish - p_start).total_seconds() / 3600 * 600)) if p_finish > p_start else 0,
             0, 0, summary_guid, b"\0" * 16, True, 1)
        pos = 2
        # tasks in ID (display) order = list order
        for tid, t in enumerate(project.tasks, start=1):
            parent_guid = summary_guid if t.parent_uid == 0 else by_uid[t.parent_uid].guid
            emit(self.proto["task"], t.uid, tid, t.name, t.start, t.finish,
                 int(round(t.duration_days * TENTHS_PER_DAY)), t.outline_level, t.parent_uid,
                 t.guid, parent_guid, t.uid in children, pos)
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
        self._set(f"{t}/FixedMeta", B.build_fixed_meta(self.task_meta_hdr, fm))
        self._set(f"{t}/Fixed2Data", fd2)
        self._set(f"{t}/Fixed2Meta", B.build_fixed_meta(self.task_meta2_hdr, fm2))
        vm, vd = B.build_var_blocks(self.task_var_hdr, var_entries)
        self._set(f"{t}/VarMeta", vm)
        self._set(f"{t}/Var2Data", vd)

        # relations
        if project.relations:
            if self.rel_proto is None:
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
            self._set(f"{c}/FixedMeta", B.build_fixed_meta(self.rel_meta_hdr, rfm))
            self._set(f"{c}/Fixed2Data", rfd2)
            self._set(f"{c}/Fixed2Meta", B.build_fixed_meta(self.rel_meta2_hdr, rfm2))

        # project properties: start date + title
        self.props[B.PROPS_PROJECT_START_DATE] = B.encode_timestamp(project.start)
        if B.PROPS_TITLE in self.props:
            self.props[B.PROPS_TITLE] = project.title.encode("utf-16-le") + b"\0" * 4   # Props strings: double NUL
        self._set(f"{PRJ}/Props", B.build_props(self.props_hdr, self.props, self.props_order))
        return write_cfb(self.root, root_clsid=PROJECT_CLSID)

    def write(self, project: Project, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.build(project))
