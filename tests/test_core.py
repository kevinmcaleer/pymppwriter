import os, struct
import olefile
import pytest
from pymppwriter.cfb import Storage, write_cfb, _name_key
from pymppwriter import blocks as B


def test_cfb_roundtrip_small_and_large_streams(tmp_path):
    root = Storage()
    root.set_path("small", b"hello")                       # mini stream
    root.set_path("dir/large", bytes(range(256)) * 40)      # > 4096 -> regular sectors
    root.set_path("dir/empty", b"")
    root.storage_path("dir/emptydir")
    out = tmp_path / "t.cfb"
    out.write_bytes(write_cfb(root))
    ole = olefile.OleFileIO(str(out))
    assert ole.openstream("small").read() == b"hello"
    assert ole.openstream("dir/large").read() == bytes(range(256)) * 40
    assert ole.get_size("dir/empty") == 0
    assert ole.get_type("dir/emptydir") == olefile.STGTY_STORAGE


def test_cfb_difat_large_file(tmp_path):
    # ~9 MB payload forces > 109 FAT sectors, engaging DIFAT sector chains
    big = (b"0123456789abcdef" * 64) * 9000       # 9,216,000 bytes
    root = Storage()
    root.set_path("big", big)
    root.set_path("dir/small", b"mini stream data")
    out = tmp_path / "big.cfb"
    out.write_bytes(write_cfb(root))
    ole = olefile.OleFileIO(str(out))
    assert ole.openstream("big").read() == big
    assert ole.openstream("dir/small").read() == b"mini stream data"


def test_validator_rejects_bad_structures():
    from datetime import datetime as D
    from pymppwriter.writer import validate, Project, Task, Relation
    ok = lambda **kw: Task(kw.pop("uid"), "t", D(2026, 1, 5, 8), D(2026, 1, 5, 17), **kw)
    validate(Project("p", D(2026, 1, 5), [ok(uid=1), ok(uid=2, outline_level=2, parent_uid=1)],
                     [Relation(1, 2)]))
    with pytest.raises(ValueError, match="duplicate"):
        validate(Project("p", D(2026, 1, 5), [ok(uid=1), ok(uid=1)]))
    with pytest.raises(ValueError, match="does not follow"):
        validate(Project("p", D(2026, 1, 5), [ok(uid=1), ok(uid=2, outline_level=3, parent_uid=1)]))
    with pytest.raises(ValueError, match="does not match the"):
        validate(Project("p", D(2026, 1, 5), [ok(uid=1), ok(uid=2), ok(uid=3, outline_level=2, parent_uid=1)]))
    with pytest.raises(ValueError, match="unknown task"):
        validate(Project("p", D(2026, 1, 5), [ok(uid=1)], [Relation(1, 9)]))
    with pytest.raises(ValueError, match="itself"):
        validate(Project("p", D(2026, 1, 5), [ok(uid=1)], [Relation(1, 1)]))
    with pytest.raises(ValueError, match="cycle"):
        validate(Project("p", D(2026, 1, 5), [ok(uid=1), ok(uid=2), ok(uid=3)],
                         [Relation(1, 2), Relation(2, 3), Relation(3, 1)]))


def test_cfb_name_ordering_uses_length_then_upper():
    assert _name_key("b") < _name_key("AA")
    assert _name_key("abc") == _name_key("ABC")


def test_props_roundtrip_and_size_header():
    B.PROPS_TYPES.clear(); B.PROPS_TYPES.update({1: 4, 2: 0})
    data = B.build_props(bytes(16), {1: b"\x01\x00\x00\x00", 2: "hi".encode("utf-16-le") + b"\0" * 4}, [1, 2])
    assert struct.unpack_from("<II", data, 0) == (len(data) - 4, len(data) - 4)
    h2, vals, order = B.parse_props(data)
    assert order == [1, 2] and vals[1] == b"\x01\x00\x00\x00" and B.PROPS_TYPES[1] == 4
    assert B.build_props(h2, vals, order) == data


def test_var_blocks_have_field_class_high_word():
    meta, var = B.build_var_blocks(bytes(24), [(1, 14, B.encode_unicode("x")), (0, 14, B.encode_unicode("y"))])
    uid0, off0, lo0, hi0 = struct.unpack_from("<IIHH", meta, 24)
    assert (uid0, lo0, hi0) == (0, 14, 0x0B40)
    assert struct.unpack_from("<I", meta, 20)[0] == len(var)


def test_timestamp_roundtrip():
    from datetime import datetime
    d = datetime(2026, 9, 7, 8, 0)
    assert B.decode_timestamp(B.encode_timestamp(d), 0) == d
    assert B.decode_timestamp(B.encode_timestamp(None), 0) is None


def test_working_tenths_standard_calendar():
    from datetime import datetime as D
    from pymppwriter.writer import working_tenths
    assert working_tenths(D(2026, 9, 7, 8), D(2026, 9, 7, 17)) == 4800      # one full day
    assert working_tenths(D(2026, 9, 7, 8), D(2026, 9, 7, 12)) == 2400      # morning only
    assert working_tenths(D(2026, 9, 7, 8), D(2026, 9, 9, 17)) == 14400     # Mon-Wed
    assert working_tenths(D(2026, 9, 4, 8), D(2026, 9, 7, 17)) == 9600      # Fri + Mon, weekend skipped
    assert working_tenths(D(2026, 9, 7, 8), D(2026, 9, 7, 8)) == 0


def test_working_tenths_custom_calendar():
    from datetime import datetime as D, date
    from pymppwriter.writer import working_tenths, Calendar, CalendarException, _work_pattern
    cal = Calendar(week={2: [(480, 720)], 5: [(540, 780)]},           # Wed half day, Sat working
                   exceptions=[CalendarException(date(2026, 9, 8), name="Hol")])
    p = _work_pattern(cal)
    assert working_tenths(D(2026, 9, 9, 8), D(2026, 9, 9, 17), p) == 2400    # Wed morning only
    assert working_tenths(D(2026, 9, 12, 0), D(2026, 9, 12, 23, 59), p) == 2400   # working Saturday
    assert working_tenths(D(2026, 9, 8, 8), D(2026, 9, 8, 17), p) == 0       # holiday Tuesday
    assert working_tenths(D(2026, 9, 7, 8), D(2026, 9, 11, 17), p) == 4800 * 3 + 2400  # 3 full days + half Wed, Tue holiday


def test_build_calendar_data_blob():
    from datetime import date
    days = [(B.CAL_DAY_DEFAULT, ())] * 7
    days[4] = (B.CAL_DAY_WORKING, [(480, 720), (780, 1020)])   # Thursday block
    days[0] = (B.CAL_DAY_NONWORKING, ())
    blob = B.build_calendar_data(days, [(date(2026, 9, 21), date(2026, 9, 21), "Hol"),
                                        (date(2026, 10, 1), date(2026, 10, 2), "Golf")])
    # "Hol\0" = 8 bytes (aligned, no pad); "Golf\0" = 10 bytes (+2 pad) + 4 closing
    assert len(blob) == 420 + 4 + 92 + 8 + 92 + 10 + 2 + 4
    assert struct.unpack_from("<HH", blob, 0) == (0, 0)          # explicit non-working
    b = blob[4 * 60:]
    assert struct.unpack_from("<HH", b, 0) == (0, 2)             # working = wire type 0
    assert struct.unpack_from("<I", b, 4)[0] == 4800             # total tenths
    assert struct.unpack_from("<HH", b, 8) == (4800, 7800)
    assert struct.unpack_from("<II", b, 20) == (2400, 2400)
    assert struct.unpack_from("<II", b, 40) == (2400, 4800)      # cumulative durations
    assert struct.unpack_from("<H", blob, 60)[0] == 1            # default day
    assert struct.unpack_from("<I", blob, 420)[0] == 2
    day = (date(2026, 9, 21) - date(1983, 12, 31)).days
    assert struct.unpack_from("<HHH", blob, 424) == (day, day, 1)
    assert struct.unpack_from("<III", blob, 424 + 72) == (1, 0, 1)
    assert struct.unpack_from("<I", blob, 424 + 84)[0] == 0x4000
    assert struct.unpack_from("<I", blob, 424 + 88)[0] == 8      # "Hol\0" utf-16
    assert blob[424 + 92:424 + 100] == "Hol\0".encode("utf-16-le")
    rec2_off = 424 + 92 + 8
    d1 = (date(2026, 10, 1) - date(1983, 12, 31)).days
    assert struct.unpack_from("<HHH", blob, rec2_off) == (d1, d1 + 1, 2)
    assert blob[-6:] == b"\0" * 6                                 # pad + closing bytes


def test_rtf_notes_and_advance_working():
    from datetime import datetime as D
    from pymppwriter.writer import encode_rtf_notes, advance_working
    rtf = encode_rtf_notes("a {b}\nc\\d é")
    assert rtf.startswith(b"{\\rtf1\\ansi")
    assert b"a \\{b\\}\\par c\\\\d \\u233?" in rtf
    assert advance_working(D(2026, 9, 7, 8), 2400) == D(2026, 9, 7, 12)      # 4h -> noon
    assert advance_working(D(2026, 9, 7, 8), 4800) == D(2026, 9, 7, 17)      # full day
    assert advance_working(D(2026, 9, 7, 8), 7200) == D(2026, 9, 8, 12)      # 1.5 days
    assert advance_working(D(2026, 9, 4, 13), 4800) == D(2026, 9, 7, 12)     # Fri pm + Mon am


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_task_fields(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task
    from pymppwriter.writer import (NATIVE, TEXT_IDS, NUMBER_IDS, DATE_IDS, FLAG_IDS,
                                    CONSTRAINT_TYPES)
    t1 = Task(1, "A", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2,
              notes="hello\nworld", wbs="X.1", constraint="SNET",
              constraint_date=D(2026, 9, 7, 8), deadline=D(2026, 9, 30, 17),
              percent_complete=50, priority=700, task_type="fixed_duration",
              effort_driven=True, text={1: "T1", 30: "T30"}, number={2: 42.5},
              date={1: D(2026, 12, 25, 8)}, flag={3: True})
    t2 = Task(2, "B", D(2026, 9, 9, 8), D(2026, 9, 9, 17), manual=True)
    p = Project("t", D(2026, 9, 7, 8), [t1, t2])
    w = MppWriter("templates/template.mpp")
    out = tmp_path / "o.mpp"
    w.write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    r = lambda s: ole.openstream("   114/TBkndTask/" + s).read()
    mh, mc, mitems = B.parse_fixed_meta_auto(r("FixedMeta"), 47)
    recs = B.split_fixed_data(r("FixedData"), mitems)
    m2h, m2c, m2items = B.parse_fixed_meta_auto(r("Fixed2Meta"), 92)
    recs2 = B.split_fixed_data(r("Fixed2Data"), m2items)
    rows = {}
    for i, rec in enumerate(recs):
        if len(rec) > 100:
            uid_it = w.task_fm[NATIVE["UNIQUE_ID"]]
            rows[struct.unpack_from("<I", rec, uid_it.offset)[0]] = i
    def fx(uid, name, fmt):
        it = w.task_fm[NATIVE[name]]
        src = recs[rows[uid]] if it.block == 0 else recs2[rows[uid]]
        return struct.unpack_from(fmt, src, it.offset)[0]
    def ts(uid, name):
        it = w.task_fm[NATIVE[name]]
        src = recs[rows[uid]] if it.block == 0 else recs2[rows[uid]]
        return B.decode_timestamp(src, it.offset)
    def bit(uid, native_id):
        i = rows[uid]
        return B.meta_bit(mitems[i], m2items[i], w.task_bit[native_id])
    # constraint, deadline, priority, type, effort-driven
    assert fx(1, "CONSTRAINT_TYPE", "<H") == CONSTRAINT_TYPES["SNET"]
    assert ts(1, "CONSTRAINT_DATE") == D(2026, 9, 7, 8)
    assert ts(1, "DEADLINE") == D(2026, 9, 30, 17)
    assert fx(1, "PRIORITY", "<H") == 700
    assert fx(1, "TYPE", "<H") == 1
    assert bit(1, NATIVE["EFFORT_DRIVEN"]) == 1 and bit(2, NATIVE["EFFORT_DRIVEN"]) == 0
    # percent complete: 50% of 2 days
    assert fx(1, "PERCENT_COMPLETE", "<H") == 50
    assert fx(1, "ACTUAL_DURATION", "<i") == 4800
    assert fx(1, "REMAINING_DURATION", "<i") == 4800
    assert ts(1, "ACTUAL_START") == D(2026, 9, 7, 8)
    assert ts(1, "STOP") == D(2026, 9, 7, 17)
    # manual scheduling on task 2
    assert bit(2, NATIVE["MANUALLY_SCHEDULED"]) == 1 and bit(1, NATIVE["MANUALLY_SCHEDULED"]) == 0
    assert fx(2, "MANUAL_DURATION", "<i") == 4800
    assert ts(2, "MANUAL_START") == D(2026, 9, 9, 8)
    # var data: notes, wbs, custom text/number/date; flags as meta bits
    _, vt, _ = B.parse_var_meta(r("VarMeta"))
    vd = r("Var2Data")
    assert B.read_var(vd, vt[1][NATIVE["NOTES"]]).startswith(b"{\\rtf1")
    assert B.decode_unicode(B.read_var(vd, vt[1][NATIVE["WBS"]])) == "X.1"
    assert B.decode_unicode(B.read_var(vd, vt[1][TEXT_IDS[0]])) == "T1"
    assert B.decode_unicode(B.read_var(vd, vt[1][TEXT_IDS[29]])) == "T30"
    assert struct.unpack("<d", B.read_var(vd, vt[1][NUMBER_IDS[1]]))[0] == 42.5
    assert B.decode_timestamp(B.read_var(vd, vt[1][DATE_IDS[0]]), 0) == D(2026, 12, 25, 8)
    assert bit(1, FLAG_IDS[2]) == 1 and bit(2, FLAG_IDS[2]) == 0
    assert NATIVE["NOTES"] not in vt[2]


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_retargets_view_scroll(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task
    w = MppWriter("templates/template.mpp")
    old = w.template_start
    p = Project("t", D(2027, 2, 1, 8), [Task(1, "A", D(2027, 2, 1, 8), D(2027, 2, 1, 17))])
    out = tmp_path / "o.mpp"
    MppWriter("templates/template.mpp").write(p, str(out))
    vd = olefile.OleFileIO(str(out)).openstream("   214/CV_iew/Var2Data").read()
    assert B.encode_timestamp(D(2027, 2, 1, 8)) in vd
    assert old not in vd


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_project_metadata(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task
    p = Project("Meta test", D(2026, 9, 7, 8),
                [Task(1, "A", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2)],
                author="Kevin McAleer", subject="Robots", keywords="robots;3dprinting",
                comments="Made by pymppwriter", manager="Kev", company="Kev's Robots",
                category="Video", status_date=D(2026, 9, 8, 17),
                currency_symbol="£", currency_code="GBP")
    out = tmp_path / "o.mpp"
    MppWriter("templates/template.mpp").write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    si = ole.openstream("\x05SummaryInformation").read()

    def lpstr(data, want_pid):
        nsec = struct.unpack_from("<I", data, 24)[0]
        for s in range(nsec):
            off = struct.unpack_from("<I", data, 44 + s * 20)[0]
            size, cnt = struct.unpack_from("<II", data, off)
            for i in range(cnt):
                pid, poff = struct.unpack_from("<II", data, off + 8 + i * 8)
                if pid == want_pid and s == 0:
                    vt, ln = struct.unpack_from("<II", data, off + poff)
                    assert vt == 30
                    return data[off + poff + 8:off + poff + 8 + ln].rstrip(b"\0").decode("cp1252")
        return None
    assert lpstr(si, 2) == "Meta test"
    assert lpstr(si, 3) == "Robots"
    assert lpstr(si, 4) == "Kevin McAleer"
    assert lpstr(si, 5) == "robots;3dprinting"
    dsi = ole.openstream("\x05DocumentSummaryInformation").read()
    assert lpstr(dsi, 14) == "Kev"
    assert lpstr(dsi, 15) == "Kev's Robots"
    _, props, _ = B.parse_props(ole.openstream("   114/Props").read())
    assert B.decode_timestamp(props[B.PROPS_PROJECT_FINISH_DATE], 0) == D(2026, 9, 8, 17)
    assert B.decode_timestamp(props[B.PROPS_STATUS_DATE], 0) == D(2026, 9, 8, 17)
    assert props[B.PROPS_CURRENCY_SYMBOL].startswith("£".encode("utf-16-le"))
    assert props[B.PROPS_CURRENCY_CODE].startswith("GBP".encode("utf-16-le"))
    assert B.PROPS_LEGACY_NEXT_UIDS not in props


def test_meta_bitmap_set_and_get_across_blocks():
    meta, meta2 = bytearray(47), bytearray(92)
    for idx in (0, 10, 311, 312, 400):
        assert B.meta_bit(meta, meta2, idx) == 0
        B.set_meta_bit(meta, meta2, idx, True)
        assert B.meta_bit(meta, meta2, idx) == 1
    assert meta[8] & 0x01                     # bit 0 -> FixedMeta byte 8
    assert meta[46] & 0x80                    # bit 311 -> last FixedMeta byte
    assert meta2[8] & 0x01                    # bit 312 -> first Fixed2Meta bitmap byte
    B.set_meta_bit(meta, meta2, 312, False)
    assert B.meta_bit(meta, meta2, 312) == 0
    assert B.meta_bit(meta, meta2, 311) == 1  # neighbour in the other block untouched


def test_build_fixed_meta_patches_count_and_data_len():
    hdr = struct.pack("<IIII", B.MAGIC, 4, 99, 12345)
    out = B.build_fixed_meta(hdr, [b"\0" * 47] * 3, data_len=618)
    assert struct.unpack_from("<II", out, 8) == (3, 618)


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_end_to_end_with_template(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task, Relation
    from pymppwriter.writer import NATIVE
    p = Project("t", D(2026, 1, 5, 8),
                [Task(1, "A", D(2026, 1, 5, 8), D(2026, 1, 6, 17), duration_days=2, outline_level=1),
                 Task(2, "B", D(2026, 1, 5, 8), D(2026, 1, 6, 17), duration_days=2, outline_level=2, parent_uid=1),
                 Task(3, "M", D(2026, 1, 7, 8), D(2026, 1, 7, 8), duration_days=0),
                 Task(4, "E", D(2026, 1, 8, 8), D(2026, 1, 8, 17), estimated=True)],
                [Relation(2, 3)])
    w = MppWriter("templates/template.mpp")
    out = tmp_path / "o.mpp"
    w.write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    r = lambda s: ole.openstream("   114/TBkndTask/" + s).read()
    mh, mc, mitems = B.parse_fixed_meta_auto(r("FixedMeta"), 47)
    recs = B.split_fixed_data(r("FixedData"), mitems)
    m2h, m2c, m2items = B.parse_fixed_meta_auto(r("Fixed2Meta"), 92)
    assert struct.unpack_from("<I", r("FixedMeta"), 12)[0] == len(r("FixedData"))
    units_it = w.task_fm[NATIVE["ACTUAL_DURATION_UNITS"]]
    dur_it = w.task_fm[NATIVE["DURATION"]]
    by_uid = {}
    for i, rec in enumerate(recs):
        if len(rec) > 100:
            by_uid[struct.unpack_from("<I", rec, 0)[0]] = i
    units = lambda uid: struct.unpack_from("<H", recs[by_uid[uid]], units_it.offset)[0]
    dur = lambda uid: struct.unpack_from("<i", recs[by_uid[uid]], dur_it.offset)[0]
    bit = lambda uid, n: B.meta_bit(mitems[by_uid[uid]], m2items[by_uid[uid]], w.task_bit[NATIVE[n]])
    assert units(0) == 0x15 and bit(0, "SUMMARY") == 1        # project summary
    assert units(1) == 0x15 and bit(1, "SUMMARY") == 1        # rolled-up summary
    assert dur(1) == 9600                                     # 2 working days from child
    assert units(2) == 0x07 and bit(2, "SUMMARY") == 0 and bit(2, "MILESTONE") == 0
    assert dur(3) == 0 and bit(3, "MILESTONE") == 1           # zero duration -> milestone
    assert units(4) == 0x27                                   # estimated flag 0x20
    if NATIVE["ESTIMATED"] in w.task_bit:                     # absent from M365 field maps
        assert bit(4, "ESTIMATED") == 1
        assert bit(2, "ESTIMATED") == 0
    # phantom assignments cleared, record counters patched
    assert ole.get_size("   114/TBkndAssn/FixedData") == 0
    assert struct.unpack_from("<I", ole.openstream("   114/TBkndAssn/FixedMeta").read(), 8)[0] == 0
    _, props, _ = B.parse_props(ole.openstream("   114/Props").read())
    assert struct.unpack("<I", props[B.PROPS_TASK_RECORD_COUNT])[0] == len(recs)
    assert struct.unpack("<I", props[B.PROPS_ASSN_RECORD_COUNT])[0] == 0
    assert struct.unpack("<I", props[B.PROPS_REL_RECORD_COUNT])[0] == 1


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_resources_and_assignments(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task, Resource, Assignment
    from pymppwriter.writer import RSC_NATIVE, ASSN_NATIVE, RSC_META_SIZE, ASSN_META_SIZE
    kev = Resource(1, "Kevin McAleer", initials="KM", email="kev@example.com")
    bot = Resource(2, "Robot", max_units=2.0)
    p = Project("t", D(2026, 1, 5, 8),
                [Task(1, "A", D(2026, 1, 5, 8), D(2026, 1, 6, 17), duration_days=2),
                 Task(2, "B", D(2026, 1, 7, 8), D(2026, 1, 7, 17), duration_days=1)],
                resources=[kev, bot],
                assignments=[Assignment(1, 1), Assignment(2, 2, units=0.5)])
    w = MppWriter("templates/template.mpp")
    out = tmp_path / "o.mpp"
    w.write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    r = lambda p_: ole.openstream("   114/" + p_).read()

    # resources: stubs + uid0 + 2 new records, counters match
    _, props, _ = B.parse_props(r("Props"))
    mh, mc, mitems = B.parse_fixed_meta_auto(r("TBkndRsc/FixedMeta"), RSC_META_SIZE)
    recs = B.split_fixed_data(r("TBkndRsc/FixedData"), mitems)
    full = {struct.unpack_from("<I", rec, 0)[0]: rec for rec in recs if len(rec) > 100}
    assert set(full) == {0, 1, 2}
    assert struct.unpack("<I", props[B.PROPS_RESOURCE_RECORD_COUNT])[0] == len(recs)
    max_units_it = w.rsc_fm[RSC_NATIVE["MAX_UNITS"]]
    assert struct.unpack_from("<d", full[2], max_units_it.offset)[0] == 20000.0
    _, vtable, _ = B.parse_var_meta(r("TBkndRsc/VarMeta"))
    vdata = r("TBkndRsc/Var2Data")
    assert B.decode_unicode(B.read_var(vdata, vtable[1][RSC_NATIVE["NAME"]])) == "Kevin McAleer"
    assert B.decode_unicode(B.read_var(vdata, vtable[1][RSC_NATIVE["INITIALS"]])) == "KM"
    assert RSC_NATIVE["INITIALS"] not in vtable[2]

    # per-resource calendars appended, pointing at the resource
    cmh, cmc, cmitems = B.parse_fixed_meta_auto(r("TBkndCal/FixedMeta"), 10)
    crecs = B.split_fixed_data(r("TBkndCal/FixedData"), cmitems)
    uc, bc, rc = w.cal_cols
    rsc_cals = {struct.unpack("<3i", rec)[rc]: struct.unpack("<3i", rec)
                for rec in crecs if len(rec) == 12}
    assert 1 in rsc_cals and 2 in rsc_cals
    assert rsc_cals[1][bc] == w.cal_standard_uid

    # assignments: 2 records with GUID cross-references and scaled units/work
    amh, amc, amitems = B.parse_fixed_meta_auto(r("TBkndAssn/FixedMeta"), ASSN_META_SIZE)
    arecs = B.split_fixed_data(r("TBkndAssn/FixedData"), amitems)
    assert len(arecs) == 2 and struct.unpack("<I", props[B.PROPS_ASSN_RECORD_COUNT])[0] == 2
    task_it = w.assn_fm[ASSN_NATIVE["TASK_UNIQUE_ID"]]
    rsc_it = w.assn_fm[ASSN_NATIVE["RESOURCE_UNIQUE_ID"]]
    units_it = w.assn_fm[ASSN_NATIVE["UNITS"]]
    work_it = w.assn_fm[ASSN_NATIVE["WORK"]]
    assert struct.unpack_from("<I", arecs[0], task_it.offset)[0] == 1
    assert struct.unpack_from("<i", arecs[0], rsc_it.offset)[0] == 1
    assert struct.unpack_from("<d", arecs[1], units_it.offset)[0] == 5000.0
    assert struct.unpack_from("<d", arecs[1], work_it.offset)[0] == 4800 * 100.0 * 0.5
    am2d = r("TBkndAssn/Fixed2Meta")
    am2n = struct.unpack_from("<I", am2d, 8)[0]
    am2items = B.parse_fixed_meta_auto(am2d, 53)[2]
    arecs2 = B.split_fixed_data(r("TBkndAssn/Fixed2Data"), am2items)
    tg = w.assn_fm[ASSN_NATIVE["TASK_GUID"]]
    rg = w.assn_fm[ASSN_NATIVE["RESOURCE_GUID"]]
    assert arecs2[0][tg.offset:tg.offset + 16] == p.tasks[0].guid
    assert arecs2[0][rg.offset:rg.offset + 16] == kev.guid
    assert arecs2[1][rg.offset:rg.offset + 16] == bot.guid
    # planned-work contour blob: Project schedules the assignment from this
    _, avt, _ = B.parse_var_meta(r("TBkndAssn/VarMeta"))
    avd = r("TBkndAssn/Var2Data")
    blob = B.read_var(avd, avt[2][ASSN_NATIVE["PLANNED_WORK_DATA"]])
    assert struct.unpack_from("<d", blob, 8)[0] == 0.5 * 10000.0 * 16     # units * 16
    assert struct.unpack_from("<d", blob, 16)[0] == 4800 * 100.0 * 0.5   # work, milli-min
    assert struct.unpack_from("<I", blob, 24)[0] == 4800 * 8             # elapsed tenths * 8
    # assigned tasks carry the work rollup (milli-minutes)
    work_it = w.task_fm[2]  # ACTUAL_WORK id 2 is at +134; WORK id 0 at +126
    work_off = w.task_fm[0].offset
    trecs = B.split_fixed_data(r("TBkndTask/FixedData"),
                               B.parse_fixed_meta_auto(r("TBkndTask/FixedMeta"), 47)[2])
    tw = {struct.unpack_from("<I", rec, 0)[0]: struct.unpack_from("<d", rec, work_off)[0]
          for rec in trecs if len(rec) > 100}
    assert tw[1] == 9600 * 100.0                  # 2 days at 100%
    assert tw[2] == 4800 * 100.0 * 0.5            # 1 day at 50%
    assert tw[0] == tw[1] + tw[2]                 # project summary rollup


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_calendars(tmp_path):
    from datetime import datetime as D, date
    from pymppwriter import MppWriter, Project, Task, Calendar, CalendarException
    from pymppwriter.writer import NATIVE, CAL_NAME_VAR, CAL_DATA_VAR
    std = Calendar(week={2: [(480, 720)]},                       # Wed half day
                   exceptions=[CalendarException(date(2026, 9, 21), name="Holiday")])
    nights = Calendar("Nights", week={0: [(1080, 1320)]})        # Mon 18:00-22:00
    p = Project("t", D(2026, 9, 7, 8),
                [Task(1, "A", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2),
                 Task(2, "B", D(2026, 9, 9, 8), D(2026, 9, 9, 17), calendar="Nights")],
                calendar=std, calendars=[nights], default_calendar="Standard")
    w = MppWriter("templates/template.mpp")
    out = tmp_path / "o.mpp"
    w.write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    r = lambda p_: ole.openstream("   114/" + p_).read()
    # Standard got a data blob; Nights got a record, name and blob
    _, vt, _ = B.parse_var_meta(r("TBkndCal/VarMeta"))
    vd = r("TBkndCal/Var2Data")
    std_uid = w.cal_standard_uid
    assert CAL_DATA_VAR in vt[std_uid]
    blob = B.read_var(vd, vt[std_uid][CAL_DATA_VAR])
    assert struct.unpack_from("<HH", blob, 3 * 60) == (0, 1)  # Wed = Sunday-first block 3, working 1 range
    nights_uid = max(uid for uid in vt if CAL_NAME_VAR in vt[uid])
    assert B.decode_unicode(B.read_var(vd, vt[nights_uid][CAL_NAME_VAR])) == "Nights"
    assert CAL_DATA_VAR in vt[nights_uid]
    cmh, cmc, cmitems = B.parse_fixed_meta_auto(r("TBkndCal/FixedMeta"), 10)
    crecs = B.split_fixed_data(r("TBkndCal/FixedData"), cmitems)
    uc = w.cal_uid_col
    rows = {struct.unpack("<3i", rec)[uc]: struct.unpack("<3i", rec) for rec in crecs if len(rec) == 12}
    assert nights_uid in rows
    assert [v for j, v in enumerate(rows[nights_uid]) if j != uc] == [-1, -1]
    assert struct.unpack_from("<I", r("TBkndCal/FixedMeta"), 12)[0] == len(r("TBkndCal/FixedData"))
    # task B references Nights
    mh, mc, mitems = B.parse_fixed_meta_auto(r("TBkndTask/FixedMeta"), 47)
    recs = B.split_fixed_data(r("TBkndTask/FixedData"), mitems)
    cal_it = w.task_fm[NATIVE["CALENDAR_UNIQUE_ID"]]
    for i, rec in enumerate(recs):
        if len(rec) > 100 and struct.unpack_from("<I", rec, 0)[0] == 2:
            assert struct.unpack_from("<i", rec, cal_it.offset)[0] == nights_uid
            m2h, _, m2items = B.parse_fixed_meta_auto(r("TBkndTask/Fixed2Meta"), 92)
            assert B.meta_bit(mitems[i], m2items[i], w.task_bit[NATIVE["CALENDAR_UNIQUE_ID"]]) == 1
    # summary rollup honours the custom week: Mon full + Tue full = 9600,
    # but Wed-half means task A staying 2 declared days is unaffected;
    # project summary spans Mon-Wed with Wed half day
    dur_it = w.task_fm[NATIVE["DURATION"]]
    uid0 = next(rec for rec in recs if len(rec) > 100 and struct.unpack_from("<I", rec, 0)[0] == 0)
    assert struct.unpack_from("<i", uid0, dur_it.offset)[0] == 4800 * 2 + 2400
    # default calendar name preserved as Standard
    _, props, _ = B.parse_props(r("Props"))
    assert props[B.PROPS_DEFAULT_CALENDAR_NAME].startswith("Standard".encode("utf-16-le"))


def test_link_driven_start_rules():
    from datetime import datetime as D
    from pymppwriter.writer import link_driven_start, Relation, _work_pattern
    pat = _work_pattern(None)
    pred = (D(2026, 9, 7, 8), D(2026, 9, 8, 17), 9600)       # Mon 08:00 -> Tue 17:00
    assert link_driven_start(Relation(1, 2), pred, 4800, pat) == D(2026, 9, 9, 8)
    assert link_driven_start(Relation(1, 2), pred, 0, pat) == D(2026, 9, 8, 17)   # milestone
    # a day of lag lands on Wednesday 17:00, which is not a start: Project rolls it on
    assert link_driven_start(Relation(1, 2, lag_days=1.0), pred, 4800, pat) == D(2026, 9, 10, 8)
    assert link_driven_start(Relation(1, 2, type="SS"), pred, 4800, pat) == D(2026, 9, 7, 8)
    assert link_driven_start(Relation(1, 2, type="FF"), pred, 4800, pat) is None


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_pins_only_starts_the_links_do_not_produce(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task, Relation
    from pymppwriter.writer import NATIVE, CONSTRAINT_TYPES
    p = Project("t", D(2026, 9, 7, 8),
                [Task(1, "A", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2),
                 Task(2, "B", D(2026, 9, 9, 8), D(2026, 9, 9, 17)),     # where the link puts it
                 Task(3, "C", D(2026, 9, 11, 8), D(2026, 9, 11, 17))],  # a typed-in later date
                [Relation(1, 2)])
    w = MppWriter("templates/template.mpp")
    out = tmp_path / "o.mpp"
    w.write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    r = lambda s: ole.openstream("   114/TBkndTask/" + s).read()
    mitems = B.parse_fixed_meta_auto(r("FixedMeta"), 47)[2]
    recs = B.split_fixed_data(r("FixedData"), mitems)
    ct = w.task_fm[NATIVE["CONSTRAINT_TYPE"]]
    con = {struct.unpack_from("<I", rec, 0)[0]: struct.unpack_from("<H", rec, ct.offset)[0]
           for rec in recs if len(rec) > 100}
    assert con[1] == CONSTRAINT_TYPES["ASAP"]       # starts with the project
    assert con[2] == CONSTRAINT_TYPES["ASAP"]       # its predecessor places it
    assert con[3] == CONSTRAINT_TYPES["SNET"]       # nothing else would hold this date


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_warns_when_links_contradict_declared_start(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task, Relation
    from pymppwriter.writer import ScheduleWarning
    p = Project("t", D(2026, 9, 7, 8),
                [Task(1, "A", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2),
                 Task(2, "B", D(2026, 9, 7, 8), D(2026, 9, 7, 17))],   # before its predecessor ends
                [Relation(1, 2)])
    with pytest.warns(ScheduleWarning, match="Project will move it"):
        MppWriter("templates/template.mpp").write(p, str(tmp_path / "o.mpp"))


# PYMPP_TEMPLATES=/path/a.mpp:/path/b.mpp exercises the writer against templates
# saved by other Project versions (2013/2016/2019/2021/M365); the record sizes
# and layouts come from each file's own headers and field maps.
@pytest.mark.skipif(not os.environ.get("PYMPP_TEMPLATES"), reason="set PYMPP_TEMPLATES to run")
@pytest.mark.parametrize("tmpl", os.environ.get("PYMPP_TEMPLATES", "").split(":"))
def test_writer_accepts_other_version_templates(tmp_path, tmpl):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task, Relation
    from pymppwriter.writer import NATIVE
    p = Project("t", D(2026, 9, 7, 8),
                [Task(1, "A", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2, outline_level=1),
                 Task(2, "B", D(2026, 9, 9, 8), D(2026, 9, 9, 17), outline_level=2, parent_uid=1)],
                [Relation(1, 2)])
    w = MppWriter(tmpl)
    out = tmp_path / (os.path.basename(tmpl) + ".out.mpp")
    w.write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    r = lambda s: ole.openstream("   114/TBkndTask/" + s).read()
    mitems = B.parse_fixed_meta_auto(r("FixedMeta"), 47)[2]
    recs = B.split_fixed_data(r("FixedData"), mitems)
    dur = w.task_fm[NATIVE["DURATION"]]
    rows = {struct.unpack_from("<I", rec, 0)[0]: rec for rec in recs if len(rec) > 100}
    assert set(rows) >= {0, 1, 2}
    assert struct.unpack_from("<i", rows[2], dur.offset)[0] == 4800     # 1 day, this file's layout
    assert struct.unpack_from("<I", r("FixedMeta"), 12)[0] == len(r("FixedData"))


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_warns_on_task_calendar_without_common_working_time(tmp_path):
    from datetime import datetime as D
    from pymppwriter import (MppWriter, Project, Task, Resource, Assignment, Calendar)
    from pymppwriter.writer import ScheduleWarning, weekly_overlap_minutes, _work_pattern
    nights = Calendar("Nights", week={0: [(1080, 1320)], 1: [(1080, 1320)], 2: [(1080, 1320)],
                                      3: [(1080, 1320)], 4: None, 5: None, 6: None})
    assert weekly_overlap_minutes(_work_pattern(nights), _work_pattern(None)) == 0
    p = Project("t", D(2026, 9, 7, 8),
                [Task(1, "A", D(2026, 9, 7, 18), D(2026, 9, 7, 22), duration_days=0.5,
                      calendar="Nights")],
                resources=[Resource(1, "Kevin")], assignments=[Assignment(1, 1)],
                calendars=[nights])
    with pytest.warns(ScheduleWarning, match="shares no working time"):
        MppWriter("templates/template.mpp").write(p, str(tmp_path / "o.mpp"))


def test_validator_handles_chains_deeper_than_the_recursion_limit():
    from datetime import datetime as D
    from pymppwriter.writer import validate, Project, Task, Relation
    n = 3000
    tasks = [Task(i, f"t{i}", D(2026, 1, 5, 8), D(2026, 1, 5, 17)) for i in range(1, n + 1)]
    rels = [Relation(i, i + 1) for i in range(1, n)]
    validate(Project("p", D(2026, 1, 5), tasks, rels))          # no RecursionError
    with pytest.raises(ValueError, match="cycle"):
        validate(Project("p", D(2026, 1, 5), tasks, rels + [Relation(n, 1)]))


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_large_project_engages_difat(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task
    note = "Generated task note. " * 60                 # ~1.2 KB of RTF each
    tasks = [Task(i, f"Task {i}", D(2027, 1, 4, 8), D(2027, 1, 4, 17), notes=note)
             for i in range(1, 4001)]
    out = tmp_path / "big.mpp"
    MppWriter("templates/template.mpp").write(Project("big", D(2027, 1, 4, 8), tasks), str(out))
    assert out.stat().st_size > 6_800_000               # more than 109 FAT sectors
    ole = olefile.OleFileIO(str(out))
    assert ole.get_size("   114/TBkndTask/Var2Data") > 4_000_000
    assert len(B.parse_fixed_meta_auto(ole.openstream("   114/TBkndTask/FixedMeta").read(), 47)[2]) > 4000


def test_next_working_moment():
    from datetime import datetime as D
    from pymppwriter.writer import next_working_moment, _work_pattern, Calendar
    assert next_working_moment(D(2026, 9, 7, 12)) == D(2026, 9, 7, 13)      # lunch boundary
    assert next_working_moment(D(2026, 9, 7, 17)) == D(2026, 9, 8, 8)       # end of day
    assert next_working_moment(D(2026, 9, 7, 9)) == D(2026, 9, 7, 9)        # already working
    assert next_working_moment(D(2026, 9, 5, 9)) == D(2026, 9, 7, 8)        # Saturday -> Monday
    half = _work_pattern(Calendar(week={2: [(480, 720)]}))                  # Wednesday half day
    assert next_working_moment(D(2026, 9, 9, 12), half) == D(2026, 9, 10, 8)


def test_previous_working_moment():
    from datetime import datetime as D
    from pymppwriter.writer import previous_working_moment, _work_pattern, Calendar
    assert previous_working_moment(D(2026, 9, 9, 8)) == D(2026, 9, 8, 17)      # Wed 08:00 -> Tue 17:00
    assert previous_working_moment(D(2026, 9, 7, 8)) == D(2026, 9, 4, 17)      # Mon -> Friday
    assert previous_working_moment(D(2026, 9, 7, 17)) == D(2026, 9, 7, 17)     # end of a window
    assert previous_working_moment(D(2026, 9, 7, 12)) == D(2026, 9, 7, 12)     # lunch boundary
    half = _work_pattern(Calendar(week={2: [(480, 720)]}))                     # Wednesday half day
    assert previous_working_moment(D(2026, 9, 10, 8), half) == D(2026, 9, 9, 12)


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_replaces_the_templates_progress_mark(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task
    from pymppwriter.writer import NATIVE
    p = Project("t", D(2026, 9, 7, 8),
                [Task(1, "Phase", D(2026, 9, 7, 8), D(2026, 9, 9, 17), outline_level=1),
                 Task(2, "A", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2,
                      outline_level=2, parent_uid=1),
                 Task(3, "B", D(2026, 9, 9, 8), D(2026, 9, 9, 17), outline_level=2, parent_uid=1)])
    w = MppWriter("templates/template.mpp")
    out = tmp_path / "o.mpp"
    w.write(p, str(out))
    ole = olefile.OleFileIO(str(out))
    r = lambda s: ole.openstream("   114/TBkndTask/" + s).read()
    mitems = B.parse_fixed_meta_auto(r("FixedMeta"), 47)[2]
    recs = B.split_fixed_data(r("FixedData"), mitems)
    m2items = B.parse_fixed_meta_auto(r("Fixed2Meta"), 92)[2]
    recs2 = B.split_fixed_data(r("Fixed2Data"), m2items)
    it = w.task_fm[NATIVE["SUMMARY_PROGRESS"]]
    prior_it = w.task_fm[NATIVE["SUMMARY_PROGRESS_PRIOR"]]        # M365 template
    rows = {struct.unpack_from("<I", rec, 0)[0]: i
            for i, rec in enumerate(recs) if len(rec) > 100}
    mark = {uid: B.decode_timestamp(recs[i], it.offset) for uid, i in rows.items()}
    prior = {uid: B.decode_timestamp(recs2[i], prior_it.offset) for uid, i in rows.items()}
    assert mark[2] == D(2026, 9, 7, 8) and mark[3] == D(2026, 9, 9, 8)   # each task's own start
    assert prior[2] == D(2026, 9, 7, 8)     # starts with the project, so no earlier moment
    assert prior[3] == D(2026, 9, 8, 17)    # the working moment before Wednesday morning
    assert mark[1] is None and mark[0] is None      # summaries carry neither
    assert prior[1] is None and prior[0] is None


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_warns_when_a_finished_task_has_assignments(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task, Resource, Assignment
    from pymppwriter.writer import ScheduleWarning
    p = Project("t", D(2026, 9, 7, 8),
                [Task(1, "done", D(2026, 9, 7, 8), D(2026, 9, 8, 17), duration_days=2,
                      percent_complete=100)],
                resources=[Resource(1, "Kevin")], assignments=[Assignment(1, 1)])
    with pytest.warns(ScheduleWarning, match="99%"):
        MppWriter("templates/template.mpp").write(p, str(tmp_path / "o.mpp"))
