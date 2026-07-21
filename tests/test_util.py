import os

import pytest

from bigfix_proxyagent.util import (write_bytes_atomic, write_json_atomic,
                                    write_text_atomic)


def test_write_bytes_is_atomic_and_creates_parents(tmp_path):
    path = tmp_path / "nested" / "blob.bin"
    write_bytes_atomic(path, b"\x00\x01binary\xff")
    assert path.read_bytes() == b"\x00\x01binary\xff"
    assert not list(path.parent.glob("*.tmp"))  # no temp file left behind


def test_write_is_atomic_and_creates_parents(tmp_path):
    path = tmp_path / "nested" / "dir" / "out.json"
    write_json_atomic(path, {"ok": True})
    assert path.read_text(encoding="utf-8") == '{\n  "ok": true\n}'
    assert not list(path.parent.glob("*.tmp"))  # no temp file left behind


def test_failed_write_cleans_up_temp_file(tmp_path, monkeypatch):
    # The rename fails; the original error must propagate and the temp file
    # must be removed rather than accumulating next to the target.
    def broken_replace(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", broken_replace)
    path = tmp_path / "out.txt"
    with pytest.raises(OSError, match="simulated rename failure"):
        write_text_atomic(path, "data")
    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_cleanup_failure_does_not_mask_original_error(tmp_path, monkeypatch):
    # Even if removing the temp file also fails, the original write error is
    # the one that surfaces.
    def broken_replace(src, dst):
        raise OSError("original")

    def broken_unlink(p):
        raise OSError("cleanup")

    monkeypatch.setattr(os, "replace", broken_replace)
    monkeypatch.setattr(os, "unlink", broken_unlink)
    with pytest.raises(OSError, match="original"):
        write_text_atomic(tmp_path / "out.txt", "data")
