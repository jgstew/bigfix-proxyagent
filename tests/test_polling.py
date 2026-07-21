import json

import pytest

from bigfix_proxyagent.config import ConfigError
from bigfix_proxyagent.polling import ScheduledPollingPlugin
from bigfix_proxyagent.state import DeviceStateStore


class Poller(ScheduledPollingPlugin):
    # ScheduledPollingPlugin leaves handle_refresh abstract; a minimal concrete
    # subclass lets us exercise the reusable helpers directly.
    def handle_refresh(self, command):  # pragma: no cover - not called here
        raise NotImplementedError


def make_poller():
    return Poller(DeviceStateStore())  # in-memory (no path)


def read_report(directory, device_id):
    return json.loads((directory / f"{device_id}.report").read_text())


# --- replay_cached_report ----------------------------------------------------


def test_replay_returns_false_without_cache(tmp_path):
    p = make_poller()
    assert p.replay_cached_report("dev1", tmp_path, sequence=None) is False
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_replay_writes_fresh_report_with_sequence_and_extra(tmp_path):
    p = make_poller()
    p.state.store_report(
        "dev1",
        {
            "device id": "dev1",
            "computer name": "c",
            "http check": {"response code": 299},
            "last server communication": "old",
        },
    )
    assert (
        p.replay_cached_report(
            "dev1", tmp_path, sequence=7, extra={"refresh interval": 30}
        )
        is True
    )
    report = read_report(tmp_path, "dev1")
    assert report["http check"]["response code"] == 299  # cached payload preserved
    assert report["last server communication"] != "old"  # freshened
    assert report["device report sequence"] == 7
    assert report["deviceReportSequence"] == 7
    assert report["refresh interval"] == 30  # extra merged in


def test_replay_without_sequence_or_extra(tmp_path):
    p = make_poller()
    p.state.store_report(
        "dev1", {"device id": "dev1", "computer name": "c", "x": 1}
    )
    assert p.replay_cached_report("dev1", tmp_path) is True
    report = read_report(tmp_path, "dev1")
    assert "device report sequence" not in report  # store_report drops sequence keys
    assert report["x"] == 1


# --- finalize_pending_deletions ----------------------------------------------


def test_finalize_removes_only_reported_and_pending():
    p = make_poller()
    p.state.mark_pending_deletion("dev1")
    p.state.mark_pending_deletion("dev2")  # pending but not reported this run
    removed = []
    p.finalize_pending_deletions(
        ["dev1", "dev2"], reported_ids={"dev1"}, remove_device=removed.append
    )
    assert removed == ["dev1"]
    assert p.state.is_pending_deletion("dev1") is False  # forgotten
    assert p.state.is_pending_deletion("dev2") is True  # untouched


def test_finalize_skips_reported_but_not_pending():
    p = make_poller()
    removed = []
    p.finalize_pending_deletions(
        ["dev1"], reported_ids={"dev1"}, remove_device=removed.append
    )
    assert removed == []


def test_finalize_config_error_keeps_device_pending():
    p = make_poller()
    p.state.mark_pending_deletion("dev1")

    def boom(device_id):
        raise ConfigError("file changed under us")

    # Must not raise, and the device stays flagged for a later retry.
    p.finalize_pending_deletions(["dev1"], reported_ids={"dev1"}, remove_device=boom)
    assert p.state.is_pending_deletion("dev1") is True


# --- construction ------------------------------------------------------------


def test_is_abstract_without_handle_refresh():
    with pytest.raises(TypeError):
        ScheduledPollingPlugin(DeviceStateStore())


def test_exposes_state():
    store = DeviceStateStore()
    assert Poller(store).state is store
