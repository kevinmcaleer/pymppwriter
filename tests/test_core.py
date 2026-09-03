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
    mh, mc, mitems = B.parse_fixed_meta(r("FixedMeta"), 47)
    recs = B.split_fixed_data(r("FixedData"), mitems)
    m2h, m2c, m2items = B.parse_fixed_meta(r("Fixed2Meta"), 92)
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
    assert units(4) == 0x27 and bit(4, "ESTIMATED") == 1      # estimated flag 0x20
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
    mh, mc, mitems = B.parse_fixed_meta(r("TBkndRsc/FixedMeta"), RSC_META_SIZE)
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
    cmh, cmc, cmitems = B.parse_fixed_meta(r("TBkndCal/FixedMeta"), 10)
    crecs = B.split_fixed_data(r("TBkndCal/FixedData"), cmitems)
    uc, bc, rc = w.cal_cols
    rsc_cals = {struct.unpack("<3i", rec)[rc]: struct.unpack("<3i", rec)
                for rec in crecs if len(rec) == 12}
    assert 1 in rsc_cals and 2 in rsc_cals
    assert rsc_cals[1][bc] == w.cal_standard_uid

    # assignments: 2 records with GUID cross-references and scaled units/work
    amh, amc, amitems = B.parse_fixed_meta(r("TBkndAssn/FixedMeta"), ASSN_META_SIZE)
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
    am2items = B.parse_fixed_meta(am2d, (len(am2d) - 16) // am2n)[2]
    arecs2 = B.split_fixed_data(r("TBkndAssn/Fixed2Data"), am2items)
    tg = w.assn_fm[ASSN_NATIVE["TASK_GUID"]]
    rg = w.assn_fm[ASSN_NATIVE["RESOURCE_GUID"]]
    assert arecs2[0][tg.offset:tg.offset + 16] == p.tasks[0].guid
    assert arecs2[0][rg.offset:rg.offset + 16] == kev.guid
    assert arecs2[1][rg.offset:rg.offset + 16] == bot.guid
    # planned-work contour blob restates the work (Project reads duration from it)
    _, avt, _ = B.parse_var_meta(r("TBkndAssn/VarMeta"))
    avd = r("TBkndAssn/Var2Data")
    blob = B.read_var(avd, avt[2][ASSN_NATIVE["PLANNED_WORK_DATA"]])
    work2 = 4800 * 100.0 * 0.5
    assert struct.unpack_from("<d", blob, 16)[0] == work2
    assert struct.unpack_from("<I", blob, 24)[0] == int(work2 * 0.08)
    assert struct.unpack_from("<d", blob, 8)[0] == 0.5 * 10000.0 * 16
