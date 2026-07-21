import json
import sqlite3

import pytest

from bigfix_proxyagent import state as state_module
from bigfix_proxyagent.state import DeviceStateStore


def test_in_memory_update_and_get():
    store = DeviceStateStore()
    assert store.get("dev") == {}
    store.update("dev", {"a": 1})
    got = store.get("dev")
    assert got == {"a": 1}
    # get() returns a copy: mutating it must not affect the store.
    got["a"] = 2
    assert store.get("dev") == {"a": 1}


def test_store_and_replay_report_strips_sequence():
    store = DeviceStateStore()
    store.store_report(
        "dev",
        {"device id": "dev", "value": 7, "device report sequence": 3,
         "deviceReportSequence": 3},
    )
    cached = store.cached_report("dev")
    assert cached == {"device id": "dev", "value": 7}
    assert store.cached_report("missing") is None


def test_pending_deletion_flag():
    store = DeviceStateStore()
    assert store.is_pending_deletion("dev") is False
    store.mark_pending_deletion("dev")
    assert store.is_pending_deletion("dev") is True


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path)
    store.mark_pending_deletion("dev")
    store.save()

    reloaded = DeviceStateStore(path)
    assert reloaded.is_pending_deletion("dev") is True


def test_save_is_merge_on_save(tmp_path):
    # Two instances change different devices; neither must lose the other's
    # write when it saves.
    path = tmp_path / "state.json"
    a = DeviceStateStore(path)
    b = DeviceStateStore(path)
    a.mark_pending_deletion("dev-a")
    b.mark_pending_deletion("dev-b")
    a.save()
    b.save()  # b loaded before a saved, but merge-on-save preserves dev-a

    final = DeviceStateStore(path)
    assert final.is_pending_deletion("dev-a") is True
    assert final.is_pending_deletion("dev-b") is True


def test_forget_removes_on_save(tmp_path):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path)
    store.mark_pending_deletion("dev")
    store.save()

    store2 = DeviceStateStore(path)
    store2.forget("dev")
    # forget also drops it from any pending update.
    store2.mark_pending_deletion("dev")
    store2.forget("dev")
    store2.save()

    assert DeviceStateStore(path).is_pending_deletion("dev") is False


def test_save_no_path_is_noop():
    DeviceStateStore().save()  # must not raise


def test_save_oserror_is_logged_not_raised(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path)
    store.mark_pending_deletion("dev")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(state_module, "write_json_atomic", boom)
    store.save()  # swallowed


def test_read_state_whitelists_and_drops_junk(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "dev": {"pending deletion": True, "junk": "ignored"},
                "cached": {"last report": {"device id": "cached"}},
                "empty": {"nothing recognized": 1},
                "not-a-dict": ["x"],
            }
        ),
        encoding="utf-8",
    )
    store = DeviceStateStore(path)
    assert store.get("dev") == {"pending deletion": True}
    assert store.cached_report("cached") == {"device id": "cached"}
    assert store.get("empty") == {}  # dropped: cleaned to nothing
    assert store.get("not-a-dict") == {}


def test_read_state_missing_or_corrupt_file(tmp_path):
    assert DeviceStateStore(tmp_path / "absent.json")._data == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert DeviceStateStore(bad)._data == {}


def test_read_state_non_object_json(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert DeviceStateStore(path)._data == {}


def test_subclass_can_extend_clean_entry(tmp_path):
    class MyStore(DeviceStateStore):
        def _clean_entry(self, entry):
            cleaned = super()._clean_entry(entry)
            if isinstance(entry.get("last check"), str):
                cleaned["last check"] = entry["last check"]
            return cleaned

    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"dev": {"last check": "T1", "pending deletion": True, "x": 9}}),
        encoding="utf-8",
    )
    store = MyStore(path)
    assert store.get("dev") == {"last check": "T1", "pending deletion": True}


# --- SQLite backend ------------------------------------------------------------


def _sqlite_sibling(json_path):
    return json_path.with_suffix(".sqlite")


def test_sqlite_backend_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path, backend="sqlite")
    store.mark_pending_deletion("dev")
    store.save()

    # The store is persisted to the SQLite sibling, not the JSON path.
    assert _sqlite_sibling(path).is_file()
    assert not path.exists()

    reloaded = DeviceStateStore(path, backend="sqlite")
    assert reloaded.is_pending_deletion("dev") is True


def test_sqlite_used_when_file_present_regardless_of_config(tmp_path):
    # A SQLite file already exists; a store defaulting to JSON must still use it.
    path = tmp_path / "state.json"
    seed = DeviceStateStore(path, backend="sqlite")
    seed.mark_pending_deletion("dev")
    seed.save()

    store = DeviceStateStore(path)  # default backend="json"
    assert store.is_pending_deletion("dev") is True
    store.mark_pending_deletion("dev2")
    store.save()

    # The write went to SQLite, not a new JSON file.
    assert not path.exists()
    assert DeviceStateStore(path).is_pending_deletion("dev2") is True


def test_migration_json_to_sqlite_seeds_and_keeps_json(tmp_path):
    path = tmp_path / "state.json"
    json_store = DeviceStateStore(path)  # JSON
    json_store.mark_pending_deletion("dev")
    json_store.store_report("cached", {"device id": "cached"})
    json_store.save()
    assert path.is_file()

    migrated = DeviceStateStore(path, backend="sqlite")
    assert migrated.is_pending_deletion("dev") is True
    assert migrated.cached_report("cached") == {"device id": "cached"}
    # Migration is one-way and non-destructive: the JSON file is left in place.
    assert path.is_file()
    assert _sqlite_sibling(path).is_file()


def test_migration_does_not_revert_to_json(tmp_path):
    # Once migrated, a later run that asks for JSON still uses SQLite.
    path = tmp_path / "state.json"
    DeviceStateStore(path).mark_pending_deletion("old")  # not saved yet
    json_store = DeviceStateStore(path)
    json_store.mark_pending_deletion("old")
    json_store.save()

    DeviceStateStore(path, backend="sqlite")  # creates the sqlite sibling

    later = DeviceStateStore(path, backend="json")
    later.mark_pending_deletion("new")
    later.save()

    # The stale JSON file never receives the new device; SQLite is authoritative.
    on_disk_json = json.loads(path.read_text(encoding="utf-8"))
    assert "new" not in on_disk_json
    assert DeviceStateStore(path).is_pending_deletion("new") is True


def test_migration_is_idempotent_and_does_not_clobber(tmp_path):
    path = tmp_path / "state.json"
    json_store = DeviceStateStore(path)
    json_store.mark_pending_deletion("dev")
    json_store.save()

    # First run migrates, then records a newer value for the same device.
    first = DeviceStateStore(path, backend="sqlite")
    first.forget("dev")
    first.save()

    # A second run still finds the JSON file; migration (INSERT OR IGNORE) must
    # not resurrect the row the first run deleted.
    second = DeviceStateStore(path, backend="sqlite")
    assert second.is_pending_deletion("dev") is False


def test_sqlite_merge_on_save(tmp_path):
    path = tmp_path / "state.json"
    DeviceStateStore(path, backend="sqlite").save()  # create the db
    a = DeviceStateStore(path)
    b = DeviceStateStore(path)
    a.mark_pending_deletion("dev-a")
    b.mark_pending_deletion("dev-b")
    a.save()
    b.save()

    final = DeviceStateStore(path)
    assert final.is_pending_deletion("dev-a") is True
    assert final.is_pending_deletion("dev-b") is True


def test_sqlite_forget_removes_on_save(tmp_path):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path, backend="sqlite")
    store.mark_pending_deletion("dev")
    store.save()

    store2 = DeviceStateStore(path)
    store2.forget("dev")
    store2.save()
    assert DeviceStateStore(path).is_pending_deletion("dev") is False


def test_sqlite_save_noop_when_nothing_changed(tmp_path):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path, backend="sqlite")
    store.save()  # no updates, no removals - must not raise or create junk
    # Reloading an empty/absent db is fine.
    assert DeviceStateStore(path)._data == {}


def test_sqlite_skips_corrupt_and_non_dict_rows(tmp_path):
    path = tmp_path / "state.json"
    DeviceStateStore(path, backend="sqlite").save()
    db = _sqlite_sibling(path)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_state "
        "(device_id TEXT PRIMARY KEY, entry TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO device_state VALUES (?, ?)",
        ("good", json.dumps({"pending deletion": True})),
    )
    conn.execute("INSERT INTO device_state VALUES (?, ?)", ("bad", "{not json"))
    conn.execute("INSERT INTO device_state VALUES (?, ?)", ("list", "[1, 2]"))
    conn.commit()
    conn.close()

    store = DeviceStateStore(path)
    assert store.is_pending_deletion("good") is True
    assert store.get("bad") == {}
    assert store.get("list") == {}


def test_sqlite_subclass_clean_entry(tmp_path):
    class MyStore(DeviceStateStore):
        def _clean_entry(self, entry):
            cleaned = super()._clean_entry(entry)
            if isinstance(entry.get("last check"), str):
                cleaned["last check"] = entry["last check"]
            return cleaned

    path = tmp_path / "state.json"
    store = MyStore(path, backend="sqlite")
    store.update("dev", {"last check": "T1", "pending deletion": True, "junk": 9})
    store.save()

    reloaded = MyStore(path)
    assert reloaded.get("dev") == {"last check": "T1", "pending deletion": True}


def test_sqlite_load_error_starts_fresh(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    DeviceStateStore(path, backend="sqlite").save()

    def boom(*a, **k):
        raise sqlite3.OperationalError("db locked")

    monkeypatch.setattr(state_module.sqlite3, "connect", boom)
    assert DeviceStateStore(path)._data == {}  # logged, not raised


def test_sqlite_save_error_logged_not_raised(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path, backend="sqlite")
    store.mark_pending_deletion("dev")

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(state_module.sqlite3, "connect", boom)
    store.save()  # swallowed


def test_migration_error_logged_not_raised(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    json_store = DeviceStateStore(path)
    json_store.mark_pending_deletion("dev")
    json_store.save()

    def boom(*a, **k):
        raise sqlite3.OperationalError("cannot create")

    monkeypatch.setattr(state_module.sqlite3, "connect", boom)
    # Migration failure must not break construction.
    store = DeviceStateStore(path, backend="sqlite")
    assert store._data == {}


def test_migration_skips_missing_or_empty_json(tmp_path):
    # backend="sqlite" with no JSON file: fresh empty store, no error.
    path = tmp_path / "state.json"
    store = DeviceStateStore(path, backend="sqlite")
    assert store._data == {}
    store.mark_pending_deletion("dev")
    store.save()
    assert DeviceStateStore(path).is_pending_deletion("dev") is True


def test_migration_from_empty_json_file(tmp_path):
    # A JSON file that is present but holds no devices: nothing to seed.
    path = tmp_path / "state.json"
    path.write_text("{}", encoding="utf-8")
    store = DeviceStateStore(path, backend="sqlite")
    assert store._data == {}


def test_migration_skips_non_dict_json_entries(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"dev": {"pending deletion": True}, "junk": ["not", "a", "dict"]}),
        encoding="utf-8",
    )
    store = DeviceStateStore(path, backend="sqlite")
    assert store.is_pending_deletion("dev") is True
    assert store.get("junk") == {}


def test_invalid_backend_falls_back_to_json(tmp_path):
    path = tmp_path / "state.json"
    store = DeviceStateStore(path, backend="nonsense")
    store.mark_pending_deletion("dev")
    store.save()
    assert path.is_file()  # wrote JSON
    assert not _sqlite_sibling(path).exists()


def test_in_memory_backend_is_none():
    store = DeviceStateStore()
    assert store._backend is None
    store.mark_pending_deletion("dev")
    store.save()  # no path, no backend - must not raise
    assert store.is_pending_deletion("dev") is True
