import os
import sys
import zipfile
from pathlib import Path

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


# --- bundled wheels (shipped inside the SDK under _vendor/) ---------------


@pytest.fixture
def no_installed_tomlkit(monkeypatch):
    """Make ``import tomlkit`` fail so the bundled-wheel path is exercised."""
    # A None entry in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "tomlkit", None)
    # Snapshot sys.path/modules so wheel entries do not leak into other tests.
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    sys.modules.pop("tomlkit", None)


def test_bundled_wheel_name_reports_tomlkit():
    name = vendor.bundled_wheel_name("tomlkit")
    assert name is not None and name.startswith("tomlkit-") and name.endswith(".whl")


def test_bundled_wheel_name_none_when_absent():
    assert vendor.bundled_wheel_name("no_such_bundled_dist") is None


def test_bundled_wheel_name_none_on_error(monkeypatch):
    def boom(_name):
        raise RuntimeError("resource lookup failed")

    monkeypatch.setattr(vendor, "_newest_bundled_resource", boom)
    assert vendor.bundled_wheel_name("tomlkit") is None


def test_load_bundled_returns_already_importable_module():
    # A stdlib module is already importable; no bundled wheel needed.
    module = vendor.load_bundled_wheel("json")
    assert module is not None and module.__name__ == "json"


def test_load_bundled_tomlkit_is_a_working_parser():
    # Installed or bundled, tomlkit resolves to a comment-preserving parser.
    module = vendor.load_bundled_wheel("tomlkit")
    assert module is not None and module.__name__ == "tomlkit"
    doc = module.parse("a = 1 # keep\n")
    doc["b"] = 2
    assert "# keep" in module.dumps(doc)


def test_bundled_wheel_path_points_at_on_disk_wheel():
    # In a source checkout the resource is a real file, so it is used directly
    # (no extraction) and is a valid wheel carrying tomlkit's package.
    path = vendor._bundled_wheel_path("tomlkit")
    assert path is not None and os.path.isfile(path) and zipfile.is_zipfile(path)
    with zipfile.ZipFile(path) as zf:
        assert any(n.startswith("tomlkit/") for n in zf.namelist())


def test_load_bundled_inserts_wheel_and_imports(tmp_path, monkeypatch):
    # Not installed -> the resolved bundled wheel is added to sys.path and
    # imported from there.
    wheel = _make_wheel(tmp_path, "bundled_demo", "bundled_demo_mod", "V = 5\n")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(vendor, "_bundled_wheel_path", lambda _name: str(wheel))
    try:
        module = vendor.load_bundled_wheel(
            "bundled_demo", import_name="bundled_demo_mod"
        )
        assert module is not None and module.V == 5
        assert str(wheel) in sys.path
    finally:
        sys.modules.pop("bundled_demo_mod", None)


def test_load_bundled_returns_none_when_absent(no_installed_tomlkit):
    assert (
        vendor.load_bundled_wheel("no_such_dist", import_name="no_such_mod_xyz")
        is None
    )


def test_load_bundled_returns_none_on_error(monkeypatch, no_installed_tomlkit):
    def boom(_name):
        raise RuntimeError("extraction failed")

    monkeypatch.setattr(vendor, "_bundled_wheel_path", boom)
    assert vendor.load_bundled_wheel("tomlkit") is None


def test_bundled_wheel_path_extracts_when_zip_backed(tmp_path, monkeypatch):
    # Simulate a zip-backed resource (os.fspath unsupported): the wheel bytes
    # must be extracted to a real temp file that then imports.
    src = _make_wheel(tmp_path, "zipbacked_demo", "zipbacked_demo_mod", "V = 7\n")
    payload = src.read_bytes()

    class FakeZipResource:
        name = "zipbacked_demo-1.0-py3-none-any.whl"

        def __fspath__(self):  # pragma: no cover - exercised via os.fspath
            raise TypeError("zip-backed resource")

        def read_bytes(self):
            return payload

    resource = FakeZipResource()
    monkeypatch.setattr(vendor, "_newest_bundled_resource", lambda _n: resource)
    monkeypatch.setattr(vendor, "_extracted_bundled", {})

    first = vendor._bundled_wheel_path("zipbacked_demo")
    assert first is not None and os.path.isfile(first)
    assert zipfile.is_zipfile(first)
    # Cached: a second call returns the same extracted path, no re-extraction.
    assert vendor._bundled_wheel_path("zipbacked_demo") == first


def test_bundled_wheel_path_none_when_missing(monkeypatch):
    monkeypatch.setattr(vendor, "_newest_bundled_resource", lambda _n: None)
    assert vendor._bundled_wheel_path("whatever") is None


# --- plugin vendor dir registration + precedence -------------------------


@pytest.fixture
def reset_plugin_vendor_dir():
    """Keep the module-level registered vendor dir from leaking between tests."""
    yield
    vendor.set_plugin_vendor_dir(None)


def test_set_and_get_plugin_vendor_dir(tmp_path, reset_plugin_vendor_dir):
    assert vendor.plugin_vendor_dir() is None
    vendor.set_plugin_vendor_dir(tmp_path)
    assert vendor.plugin_vendor_dir() == Path(tmp_path)
    vendor.set_plugin_vendor_dir(None)
    assert vendor.plugin_vendor_dir() is None


def test_or_bundled_prefers_loose_vendor_wheel(monkeypatch):
    loose, bundled = object(), object()
    monkeypatch.setattr(vendor, "load_wheel", lambda *a, **k: loose)
    monkeypatch.setattr(vendor, "load_bundled_wheel", lambda *a, **k: bundled)
    assert vendor.load_wheel_or_bundled("x", "/some/vendor") is loose


def test_or_bundled_falls_back_to_bundled(monkeypatch):
    bundled = object()
    monkeypatch.setattr(vendor, "load_wheel", lambda *a, **k: None)
    monkeypatch.setattr(vendor, "load_bundled_wheel", lambda *a, **k: bundled)
    assert vendor.load_wheel_or_bundled("x", "/some/vendor") is bundled


def test_or_bundled_no_dir_skips_loose(monkeypatch, reset_plugin_vendor_dir):
    bundled, consulted = object(), []
    monkeypatch.setattr(vendor, "load_wheel", lambda *a, **k: consulted.append(1))
    monkeypatch.setattr(vendor, "load_bundled_wheel", lambda *a, **k: bundled)
    vendor.set_plugin_vendor_dir(None)
    assert vendor.load_wheel_or_bundled("x") is bundled
    assert consulted == []  # no dir given or registered -> loose path skipped


def test_or_bundled_defaults_to_registered_dir(
    monkeypatch, tmp_path, reset_plugin_vendor_dir
):
    seen = {}

    def fake_load_wheel(name, vendor_dir, *, import_name=None):
        seen["vendor_dir"] = vendor_dir
        return None

    monkeypatch.setattr(vendor, "load_wheel", fake_load_wheel)
    monkeypatch.setattr(vendor, "load_bundled_wheel", lambda *a, **k: "bundled")
    vendor.set_plugin_vendor_dir(tmp_path)
    vendor.load_wheel_or_bundled("x")
    assert seen["vendor_dir"] == Path(tmp_path)


def test_or_bundled_loads_real_loose_wheel(tmp_path):
    # End-to-end (not monkeypatched): a loose wheel in vendor_dir is imported.
    wheel = _make_wheel(tmp_path, "orbdist", "orbdist_mod", "V = 3\n")
    try:
        module = vendor.load_wheel_or_bundled(
            "orbdist", tmp_path, import_name="orbdist_mod"
        )
        assert module is not None and module.V == 3
    finally:
        sys.modules.pop("orbdist_mod", None)
        if str(wheel) in sys.path:
            sys.path.remove(str(wheel))


def test_or_bundled_tomlkit_is_a_working_parser():
    # With no vendor dir, tomlkit resolves (installed or bundled) and parses.
    module = vendor.load_wheel_or_bundled("tomlkit")
    assert module is not None and module.__name__ == "tomlkit"


def test_or_bundled_falls_through_when_cached_wheel_unloadable(monkeypatch):
    # cache reports success but the wheel still will not import -> bundled.
    sentinel = object()
    monkeypatch.setattr(vendor, "load_wheel", lambda *a, **k: None)
    monkeypatch.setattr(vendor, "_cache_bundled_in_vendor", lambda *a, **k: True)
    monkeypatch.setattr(vendor, "load_bundled_wheel", lambda *a, **k: sentinel)
    assert vendor.load_wheel_or_bundled("x", "/some/vendor") is sentinel


# --- caching the bundled wheel into the plugin's vendor/ ------------------


class _FakeZipResource:
    """A bundled resource backed by a zip (no real filesystem path)."""

    def __init__(self, name, payload) -> None:
        self.name = name
        self._payload = payload

    def __fspath__(self):  # pragma: no cover - exercised via os.fspath
        raise TypeError("zip-backed resource")

    def read_bytes(self):
        return self._payload


def test_cache_bundled_skips_on_disk_wheel(tmp_path):
    # In a source checkout the bundled tomlkit is a real file, so nothing is
    # copied (load_bundled_wheel loads it directly).
    assert vendor._cache_bundled_in_vendor("tomlkit", tmp_path) is False
    assert list(tmp_path.iterdir()) == []


def test_cache_bundled_none_when_absent(tmp_path):
    assert vendor._cache_bundled_in_vendor("no_such_bundled", tmp_path) is False


def test_cache_bundled_returns_false_on_error(monkeypatch, tmp_path):
    def boom(_name):
        raise RuntimeError("resource lookup failed")

    monkeypatch.setattr(vendor, "_newest_bundled_resource", boom)
    assert vendor._cache_bundled_in_vendor("tomlkit", tmp_path) is False


def test_cache_bundled_writes_zip_backed_wheel(tmp_path, monkeypatch):
    src = _make_wheel(tmp_path, "cachedist", "cachedist_mod", "V = 1\n")
    resource = _FakeZipResource("cachedist-1.0-py3-none-any.whl", src.read_bytes())
    monkeypatch.setattr(vendor, "_newest_bundled_resource", lambda _n: resource)

    dest = tmp_path / "vendor"
    assert vendor._cache_bundled_in_vendor("cachedist", dest) is True
    written = dest / "cachedist-1.0-py3-none-any.whl"
    assert written.is_file() and zipfile.is_zipfile(written)
    # Already present: still True, and the bytes are untouched (no rewrite).
    before = written.read_bytes()
    assert vendor._cache_bundled_in_vendor("cachedist", dest) is True
    assert written.read_bytes() == before


def test_or_bundled_caches_then_loads_from_vendor(tmp_path, monkeypatch):
    # End-to-end: not installed, not yet vendored -> the bundled (zip-backed)
    # wheel is copied into vendor/ and then imported directly from there.
    src = _make_wheel(tmp_path, "e2edist", "e2edist_mod", "V = 9\n")
    resource = _FakeZipResource("e2edist-1.0-py3-none-any.whl", src.read_bytes())
    monkeypatch.setattr(vendor, "_newest_bundled_resource", lambda _n: resource)
    monkeypatch.setattr(sys, "path", list(sys.path))
    dest = tmp_path / "vendor"
    try:
        module = vendor.load_wheel_or_bundled(
            "e2edist", dest, import_name="e2edist_mod"
        )
        assert module is not None and module.V == 9
        cached = dest / "e2edist-1.0-py3-none-any.whl"
        assert cached.is_file()
        # Loaded from the cached vendor file, not a temp extraction.
        assert str(cached) in module.__file__
    finally:
        sys.modules.pop("e2edist_mod", None)
