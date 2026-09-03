"""Command-line entry point: build an .mpp from a JSON project description.

    pymppwriter build project.json --template template.mpp --out plan.mpp
    pymppwriter inspect plan.mpp
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime

from .writer import MppWriter, Project, Task, Relation


def _dt(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {s!r} (use YYYY-MM-DD or YYYY-MM-DDTHH:MM)")


def load_project(path: str) -> Project:
    spec = json.load(open(path, encoding="utf-8"))
    tasks = [Task(uid=t["uid"], name=t["name"], start=_dt(t["start"]), finish=_dt(t["finish"]),
                  duration_days=t.get("duration_days", 1.0), outline_level=t.get("outline_level", 1),
                  parent_uid=t.get("parent_uid", 0), duration_units=t.get("duration_units", "d"),
                  estimated=t.get("estimated", False)) for t in spec["tasks"]]
    rels = [Relation(r["pred"], r["succ"], r.get("type", "FS"), r.get("lag_days", 0.0)) for r in spec.get("links", [])]
    return Project(spec["title"], _dt(spec["start"]), tasks, rels)


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
