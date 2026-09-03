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


@pytest.mark.skipif(not os.path.exists("templates/template.mpp"), reason="needs templates/template.mpp")
def test_writer_end_to_end_with_template(tmp_path):
    from datetime import datetime as D
    from pymppwriter import MppWriter, Project, Task, Relation
    p = Project("t", D(2026, 1, 5, 8), [Task(1, "A", D(2026, 1, 5, 8), D(2026, 1, 5, 17)),
                                        Task(2, "B", D(2026, 1, 6, 8), D(2026, 1, 6, 17))], [Relation(1, 2)])
    out = tmp_path / "o.mpp"
    MppWriter("templates/template.mpp").write(p, str(out))
    assert olefile.OleFileIO(str(out)).exists("   114/TBkndTask/FixedData")
