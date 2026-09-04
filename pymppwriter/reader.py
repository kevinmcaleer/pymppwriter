"""Read an .mpp back into the Project model.

Field-map driven, exactly like the writer: every offset comes from the file's
own `Props` field maps and every flag from its meta bitmaps, so a file saved by
any MPP14-era Project (2010 through the current Microsoft 365 client) reads
correctly — there are no hard-coded record layouts to go stale between
versions.

    from pymppwriter import read_project
    project = read_project("plan.mpp")
    for task in project.tasks:
        print(task.uid, task.name, task.duration_days)

What comes back is the same `Project` the writer consumes, so a file can be
read, edited and written again. Fields the writer does not model (baselines,
costs, timephased data) are not returned.
"""
from __future__ import annotations
import struct
from datetime import datetime
from typing import Dict, List, Optional

from .cfb import load_cfb
from . import blocks as B
from .writer import (PRJ, NATIVE, RSC_NATIVE, ASSN_NATIVE, REL_TYPES, CONSTRAINT_TYPES,
                     TASK_BASELINE_IDS, Baseline, WORK_SCALE,
                     TASK_META_SIZE, TASK_META2_SIZE, REL_META_SIZE, RSC_META_SIZE,
                     ASSN_META_SIZE, TENTHS_PER_DAY, UNITS_CODES, PCT_SCALE,
                     ESTIMATED_FLAG, Project, Task, Relation, Resource, Assignment,
                     decode_rtf_notes)

_REL_TYPE_NAMES = {v: k for k, v in REL_TYPES.items()}
_CONSTRAINT_NAMES = {v: k for k, v in CONSTRAINT_TYPES.items()}
_UNIT_NAMES = {v: k for k, v in UNITS_CODES.items()}


class MppReadError(RuntimeError):
    """The file is not a project we can read."""


class _Klass:
    """One entity class (tasks, resources, …) with its records and field map."""

    def __init__(self, reader, storage: str, meta_size: int, meta2_size: int, props_key: int):
        self.fm, self.bit = reader._class_map(props_key)
        self.recs, self.metas, self.recs2, self.metas2 = [], [], [], []
        try:
            meta = reader._get(f"{storage}/FixedMeta")
            data = reader._get(f"{storage}/FixedData")
        except KeyError:
            return
        self.metas = B.parse_fixed_meta_auto(meta, meta_size)[2]
        self.recs = B.split_fixed_data(data, self.metas)
        try:
            self.metas2 = B.parse_fixed_meta_auto(reader._get(f"{storage}/Fixed2Meta"), meta2_size)[2]
            self.recs2 = B.split_fixed_data(reader._get(f"{storage}/Fixed2Data"), self.metas2)
        except KeyError:
            pass
        self.vtable, self.vdata = {}, b""
        try:
            self.vtable = B.parse_var_meta(reader._get(f"{storage}/VarMeta"))[1]
            self.vdata = reader._get(f"{storage}/Var2Data")
        except KeyError:
            pass

    def value(self, i: int, native_id: int, fmt: str):
        it = self.fm.get(native_id)
        if it is None:
            return None
        src = self.recs[i] if it.block == 0 else (self.recs2[i] if i < len(self.recs2) else b"")
        size = struct.calcsize(fmt)
        if it.offset + size > len(src):
            return None
        return struct.unpack_from(fmt, src, it.offset)[0]

    def timestamp(self, i: int, native_id: int) -> Optional[datetime]:
        it = self.fm.get(native_id)
        if it is None:
            return None
        src = self.recs[i] if it.block == 0 else (self.recs2[i] if i < len(self.recs2) else b"")
        if it.offset + 4 > len(src):
            return None
        return B.decode_timestamp(src, it.offset)

    def flag(self, i: int, native_id: int) -> bool:
        idx = self.bit.get(native_id)
        if idx is None or i >= len(self.metas):
            return False
        meta2 = self.metas2[i] if i < len(self.metas2) else b""
        return bool(B.meta_bit(self.metas[i], meta2, idx))

    def var(self, uid: int, native_id: int) -> Optional[bytes]:
        entry = self.vtable.get(uid, {}).get(native_id)
        return B.read_var(self.vdata, entry) if entry is not None else None

    def text(self, uid: int, native_id: int) -> str:
        raw = self.var(uid, native_id)
        return B.decode_unicode(raw) if raw else ""


class _Reader:
    def __init__(self, path: str):
        self.root = load_cfb(path)
        try:
            _, self.props, _ = B.parse_props(self._get(f"{PRJ}/Props"))
        except KeyError as exc:
            raise MppReadError(
                f"{path!r} has no {PRJ}/Props stream — not an MPP14 project file"
            ) from exc

    def _get(self, path: str) -> bytes:
        node = self.root
        parts = path.split("/")
        for p in parts[:-1]:
            node = node.children[p]
        return node.children[parts[-1]]

    def _class_map(self, props_key: int):
        fm, bit = {}, {}
        if props_key not in self.props:
            return fm, bit
        for i, it in enumerate(B.parse_field_map(self.props[props_key])):
            tid = it.type_value & 0xFFFF
            if it.in_fixed:
                fm.setdefault(tid, it)
            bit.setdefault(tid, i)
        return fm, bit


def _props_text(props: Dict[int, bytes], key: int) -> str:
    raw = props.get(key)
    return B.decode_unicode(raw) if raw else ""


def read_project(path: str) -> Project:
    """Read an .mpp file into a Project.

    Raises MppReadError if the file is not an MPP14 project.
    """
    r = _Reader(path)
    tasks_k = _Klass(r, f"{PRJ}/TBkndTask", TASK_META_SIZE, TASK_META2_SIZE, B.PROPS_TASK_FIELD_MAP)
    rsc_k = _Klass(r, f"{PRJ}/TBkndRsc", RSC_META_SIZE, 53, B.PROPS_RESOURCE_FIELD_MAP)
    assn_k = _Klass(r, f"{PRJ}/TBkndAssn", ASSN_META_SIZE, 53, B.PROPS_ASSIGNMENT_FIELD_MAP)

    tasks: List[Task] = []
    for i, rec in enumerate(tasks_k.recs):
        if len(rec) <= 100:            # stub rows carry no fields
            continue
        uid = tasks_k.value(i, NATIVE["UNIQUE_ID"], "<I")
        if uid is None or uid == 0:    # uid 0 is the project summary row
            continue
        start = tasks_k.timestamp(i, NATIVE["START"])
        finish = tasks_k.timestamp(i, NATIVE["FINISH"])
        if start is None or finish is None:
            continue
        tenths = tasks_k.value(i, NATIVE["DURATION"], "<i") or 0
        units_word = tasks_k.value(i, NATIVE["ACTUAL_DURATION_UNITS"], "<H") or 0
        constraint = _CONSTRAINT_NAMES.get(tasks_k.value(i, NATIVE["CONSTRAINT_TYPE"], "<H"))
        notes_raw = tasks_k.var(uid, NATIVE["NOTES"])
        tasks.append(Task(
            uid=uid,
            name=tasks_k.text(uid, NATIVE["NAME"]),
            start=start,
            finish=finish,
            duration_days=round(tenths / TENTHS_PER_DAY, 4),
            outline_level=tasks_k.value(i, NATIVE["OUTLINE_LEVEL"], "<H") or 1,
            parent_uid=tasks_k.value(i, NATIVE["PARENT_UID"], "<I") or 0,
            duration_units=_UNIT_NAMES.get(units_word & ~ESTIMATED_FLAG, "d"),
            estimated=bool(units_word & ESTIMATED_FLAG),
            notes=decode_rtf_notes(notes_raw) if notes_raw else "",
            wbs=tasks_k.text(uid, NATIVE["WBS"]) or None,
            constraint=constraint if constraint != "ASAP" else None,
            constraint_date=tasks_k.timestamp(i, NATIVE["CONSTRAINT_DATE"]),
            baselines=_read_baselines(tasks_k, uid),
            percent_complete=tasks_k.value(i, NATIVE["PERCENT_COMPLETE"], "<H") or 0,
            priority=tasks_k.value(i, NATIVE["PRIORITY"], "<H") or 500,
            manual=tasks_k.flag(i, NATIVE["MANUALLY_SCHEDULED"]),
        ))

    resources: List[Resource] = []
    for i, rec in enumerate(rsc_k.recs):
        if len(rec) <= 100:
            continue
        uid = rsc_k.value(i, RSC_NATIVE["UNIQUE_ID"], "<I")
        name = rsc_k.text(uid, RSC_NATIVE["NAME"]) if uid is not None else ""
        if not uid or not name:        # uid 0 is Project's unnamed placeholder
            continue
        max_units = rsc_k.value(i, RSC_NATIVE["MAX_UNITS"], "<d")
        resources.append(Resource(
            uid=uid,
            name=name,
            initials=rsc_k.text(uid, RSC_NATIVE["INITIALS"]),
            email=rsc_k.text(uid, RSC_NATIVE["EMAIL_ADDRESS"]),
            max_units=round((max_units or PCT_SCALE) / PCT_SCALE, 4),
        ))

    assignments: List[Assignment] = []
    for i, rec in enumerate(assn_k.recs):
        task_uid = assn_k.value(i, ASSN_NATIVE["TASK_UNIQUE_ID"], "<I")
        rsc_uid = assn_k.value(i, ASSN_NATIVE["RESOURCE_UNIQUE_ID"], "<i")
        if not task_uid or rsc_uid is None or rsc_uid <= 0:
            continue
        units = assn_k.value(i, ASSN_NATIVE["UNITS"], "<d")
        assignments.append(Assignment(
            task_uid=task_uid,
            resource_uid=rsc_uid,
            units=round((units or PCT_SCALE) / PCT_SCALE, 4),
        ))

    relations = _read_relations(r)

    start_raw = r.props.get(B.PROPS_PROJECT_START_DATE)
    project_start = (B.decode_timestamp(start_raw, 0) if start_raw else None) or (
        min((t.start for t in tasks), default=datetime.now())
    )
    return Project(
        title=_props_text(r.props, B.PROPS_TITLE) or "Project",
        start=project_start,
        tasks=tasks,
        relations=relations,
        resources=resources,
        assignments=assignments,
    )


def _read_baselines(tasks_k: _Klass, uid: int) -> Dict[int, Baseline]:
    """Saved baselines for one task, by slot.

    They live in var data — the fixed baseline fields stay empty in files
    Project writes — and a cleared baseline keeps its entries with the dates
    at NA and the numbers at zero, so those are skipped.
    """
    out: Dict[int, Baseline] = {}
    for slot, ids in enumerate(TASK_BASELINE_IDS):
        start = tasks_k.var(uid, ids["start"])
        finish = tasks_k.var(uid, ids["finish"])
        if start is None and finish is None:
            continue
        b = Baseline(
            start=B.decode_timestamp(start, 0) if start else None,
            finish=B.decode_timestamp(finish, 0) if finish else None,
        )
        dur = tasks_k.var(uid, ids["duration"])
        if dur and len(dur) >= 4:
            b.duration_days = round(struct.unpack("<i", dur[:4])[0] / TENTHS_PER_DAY, 4)
        work = tasks_k.var(uid, ids["work"])
        if work and len(work) >= 8:
            b.work_hours = round(struct.unpack("<d", work[:8])[0] / (WORK_SCALE * 600), 4)
        cost = tasks_k.var(uid, ids["cost"])
        if cost and len(cost) >= 8:
            b.cost = struct.unpack("<d", cost[:8])[0]
        if b.start is None and b.finish is None and not b.duration_days and not b.work_hours:
            continue                     # a cleared slot, not a saved one
        out[slot] = b
    return out


def _read_relations(r: _Reader) -> List[Relation]:
    """Dependencies from TBkndCons.

    Only the first three dwords (uid, predecessor, successor) and the type word
    are mapped; the trailer holding lag moved between eras, so it is read the
    way the writer writes it — 2010 files put lag units first, M365 the lag.
    """
    rel_fm, _ = r._class_map(B.PROPS_RELATION_FIELD_MAP)
    is_2010 = rel_fm.get(9) is not None and rel_fm[9].offset == 0
    try:
        metas = B.parse_fixed_meta_auto(r._get(f"{PRJ}/TBkndCons/FixedMeta"), REL_META_SIZE)[2]
        recs = B.split_fixed_data(r._get(f"{PRJ}/TBkndCons/FixedData"), metas)
    except KeyError:
        return []
    out = []
    for rec in recs:
        if len(rec) < 20:
            continue
        _, pred, succ = struct.unpack_from("<III", rec, 0)
        if not pred or not succ:
            continue
        kind = _REL_TYPE_NAMES.get(struct.unpack_from("<H", rec, 12)[0], "FS")
        lag = struct.unpack_from("<i", rec, 16 if is_2010 else 14)[0]
        out.append(Relation(pred, succ, type=kind, lag_days=round(lag / TENTHS_PER_DAY, 4)))
    return out
