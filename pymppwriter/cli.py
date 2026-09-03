"""Command-line entry point: build an .mpp from a JSON project description.

    pymppwriter build project.json --template template.mpp --out plan.mpp
    pymppwriter inspect plan.mpp
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime

from .writer import (MppWriter, Project, Task, Relation, Resource, Assignment,
                     Calendar, CalendarException)

DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _calendar(spec: dict, name: str = "Standard") -> Calendar:
    week = {}
    for day, val in spec.get("week", {}).items():
        wd = DAY_NAMES[day.lower()[:3]]
        week[wd] = None if val is None else [(_minutes(a), _minutes(b)) for a, b in val]
    excs = []
    for x in spec.get("holidays", []):
        if isinstance(x, str):
            excs.append(CalendarException(_dt(x).date()))
        else:
            excs.append(CalendarException(_dt(x["from"]).date(),
                                          _dt(x.get("to", x["from"])).date(),
                                          x.get("name", "")))
    return Calendar(spec.get("name", name), week, excs)


def _dt(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {s!r} (use YYYY-MM-DD or YYYY-MM-DDTHH:MM)")


def load_project(path: str) -> Project:
    spec = json.load(open(path, encoding="utf-8"))
    def _task(t: dict) -> Task:
        custom = {k: {int(n): v for n, v in t.get(k, {}).items()} for k in ("text", "number", "flag")}
        dates = {int(n): _dt(v) for n, v in t.get("date", {}).items()}
        return Task(uid=t["uid"], name=t["name"], start=_dt(t["start"]), finish=_dt(t["finish"]),
                    duration_days=t.get("duration_days", 1.0), outline_level=t.get("outline_level", 1),
                    parent_uid=t.get("parent_uid", 0), duration_units=t.get("duration_units", "d"),
                    estimated=t.get("estimated", False), calendar=t.get("calendar"),
                    notes=t.get("notes", ""), wbs=t.get("wbs"),
                    constraint=t.get("constraint"),
                    constraint_date=_dt(t["constraint_date"]) if "constraint_date" in t else None,
                    deadline=_dt(t["deadline"]) if "deadline" in t else None,
                    percent_complete=t.get("percent_complete", 0),
                    priority=t.get("priority", 500), task_type=t.get("task_type", "fixed_units"),
                    effort_driven=t.get("effort_driven", False), manual=t.get("manual", False),
                    text=custom["text"], number=custom["number"], date=dates, flag=custom["flag"])

    tasks = [_task(t) for t in spec["tasks"]]
    rels = [Relation(r["pred"], r["succ"], r.get("type", "FS"), r.get("lag_days", 0.0)) for r in spec.get("links", [])]
    rscs = [Resource(uid=r["uid"], name=r["name"], initials=r.get("initials", ""),
                     email=r.get("email", ""), max_units=r.get("max_units", 1.0))
            for r in spec.get("resources", [])]
    assns = [Assignment(a["task"], a["resource"], a.get("units", 1.0)) for a in spec.get("assignments", [])]
    cal = _calendar(spec["calendar"]) if "calendar" in spec else None
    cals = [_calendar(c, c["name"]) for c in spec.get("calendars", [])]
    return Project(spec["title"], _dt(spec["start"]), tasks, rels, rscs, assns,
                   calendar=cal, calendars=cals, default_calendar=spec.get("default_calendar"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pymppwriter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build an .mpp from a JSON spec")
    b.add_argument("spec"); b.add_argument("--template", required=True); b.add_argument("--out", required=True)
    i = sub.add_parser("inspect", help="dump the OLE stream tree of an .mpp")
    i.add_argument("mpp")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        MppWriter(a.template).write(load_project(a.spec), a.out)
        print(f"wrote {a.out}")
    else:
        import olefile
        ole = olefile.OleFileIO(a.mpp)
        for e in ole.listdir(streams=True, storages=True):
            p = "/".join(e)
            size = ole.get_size(p) if ole.get_type(p) == olefile.STGTY_STREAM else "<dir>"
            print(f"{str(size):>8}  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
