import sys
import zipfile

import pytest

from bigfix_proxyagent import vendor


def test_returns_already_importable_module(tmp_path):
    # A stdlib module is already importable; no wheel needed, no sys.path edit.
    before = list(sys.path)
    module = vendor.load_wheel("json", tmp_path)
    assert module is not None
    assert module.__name__ == "json"
    assert sys.path == before


def test_import_name_differs_from_distribution_name(tmp_path):
    # e.g. distribution "not-json" but import "json".
    module = vendor.load_wheel("anything", tmp_path, import_name="json")
    assert module is module and module.__name__ == "json"


def test_returns_none_when_absent_and_no_wheel(tmp_path):
    assert vendor.load_wheel("no_such_package_xyz", tmp_path) is None


def _make_wheel(dir_path, dist, module_name, body):
    wheel = dir_path / f"{dist}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(f"{module_name}.py", body)
    return wheel


def test_loads_module_from_vendored_wheel(tmp_path):
    dist = "vendored_pkg_demo"
    module_name = "vendored_pkg_demo_mod"
    wheel = _make_wheel(tmp_path, dist, module_name, "VALUE = 42\n")
    try:
        module = vendor.load_wheel(dist, tmp_path, import_name=module_name)
        assert module is not None
        assert module.VALUE == 42
        assert str(wheel) in sys.path
        # Calling again is a no-op on sys.path (already importable now).
        length = len(sys.path)
        assert vendor.load_wheel(dist, tmp_path, import_name=module_name) is module
        assert len(sys.path) == length
    finally:
        sys.modules.pop(module_name, None)
        if str(wheel) in sys.path:
            sys.path.remove(str(wheel))


def test_newest_wheel_wins(tmp_path):
    _make_wheel(tmp_path, "multi_demo", "multi_demo_a", "V = 1\n")
    # A higher version sorts last; its module is the one imported.
    (tmp_path / "multi_demo-2.0-py3-none-any.whl").write_bytes(
        (tmp_path / "multi_demo-1.0-py3-none-any.whl").read_bytes()
    )
    assert vendor.vendored_wheel_name("multi_demo", tmp_path) == (
        "multi_demo-2.0-py3-none-any.whl"
    )


def test_corrupt_wheel_returns_none(tmp_path):
    # A file that matches the glob but is not a valid importable wheel.
    (tmp_path / "brokenpkg-1.0-py3-none-any.whl").write_text("not a zip")
    assert vendor.load_wheel("brokenpkg", tmp_path, import_name="brokenpkg_x") is None
    if str(tmp_path / "brokenpkg-1.0-py3-none-any.whl") in sys.path:
        sys.path.remove(str(tmp_path / "brokenpkg-1.0-py3-none-any.whl"))


def test_vendored_wheel_name_none_when_absent(tmp_path):
    assert vendor.vendored_wheel_name("missing", tmp_path) is None
