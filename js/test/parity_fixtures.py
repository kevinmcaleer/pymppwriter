"""Emit block-layer outputs from the Python implementation, as JSON hex.

The TypeScript parity test computes the same values and compares bytes, so a
divergence between the two implementations fails a test rather than surfacing
as a file Microsoft Project rejects months later.
"""
import json
import sys
from datetime import date, datetime

sys.path.insert(0, sys.argv[1])          # the repo root
from pymppwriter import blocks as B      # noqa: E402
from pymppwriter.cfb import load_cfb     # noqa: E402
from pymppwriter.writer import encode_rtf_notes   # noqa: E402

template = sys.argv[2]
root = load_cfb(template)
prj = root.children["   114"]
task = prj.children["TBkndTask"]

out = {}

# Props: parse and rebuild unchanged, then with a patched title
hdr, props, order = B.parse_props(prj.children["Props"])
out["props_rebuild"] = B.build_props(hdr, props, order).hex()
patched = dict(props)
patched[B.PROPS_TITLE] = "Parity".encode("utf-16-le") + b"\0" * 4
out["props_patched"] = B.build_props(hdr, patched, order).hex()

# var blocks from the template's own task names, plus a new entry
vh, vtable, ventries = B.parse_var_meta(task.children["VarMeta"])
vdata = task.children["Var2Data"]
values = [(uid, typ, B.read_var(vdata, off)) for uid, off, typ, _ in ventries]
values.append((99, 15, encode_rtf_notes("parity note")))
meta, data = B.build_var_blocks(vh, values)
out["var_meta"] = meta.hex()
out["var_data"] = data.hex()

# fixed meta rebuilt with a patched count and data length
mh, count, items = B.parse_fixed_meta_auto(task.children["FixedMeta"], 47)
out["fixed_meta"] = B.build_fixed_meta(mh, items, 4321).hex()

# calendar blob: a half day, a non-working day, two exceptions
days = [(B.CAL_DAY_DEFAULT, ())] * 7
days[3] = (B.CAL_DAY_WORKING, [(480, 720), (780, 1020)])
days[0] = (B.CAL_DAY_NONWORKING, ())
out["calendar"] = B.build_calendar_data(days, [
    (date(2026, 9, 21), date(2026, 9, 21), "Hol"),
    (date(2026, 10, 1), date(2026, 10, 2), "Golf"),
]).hex()

# timestamps across boundaries, and the NA marker
stamps = [datetime(2026, 9, 7, 8, 0), datetime(1984, 1, 1, 0, 0),
          datetime(2027, 3, 15, 22, 0), datetime(2026, 12, 31, 23, 59), None]
out["timestamps"] = [B.encode_timestamp(s).hex() for s in stamps]

# strings
out["unicode"] = [B.encode_unicode(s).hex() for s in ("Design", "Café £", "")]

# an OLE property set with several fields replaced
si = root.children["\x05SummaryInformation"]
out["summary_info"] = B.update_property_set_strings(
    si, {2: "Parity title", 3: "Subject", 4: "Kevin McAleer", 5: "a;b"}).hex()

json.dump(out, sys.stdout)
