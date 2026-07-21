import json

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
