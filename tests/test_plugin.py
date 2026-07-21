import json
import os

import pytest

from bigfix_proxyagent.command import Command
from bigfix_proxyagent.plugin import ProxyAgentPlugin


class DemoPlugin(ProxyAgentPlugin):
    """Minimal concrete plugin: one device, one supported action."""

    def __init__(self):
        self.refreshed = []
        self.deleted = []

    def handle_refresh(self, command):
        self.refreshed.append(command)
        report = {"device id": "dev1", "computer name": "demo", "data source": "demo"}
        self.write_report(command.output_directory, report)
        self.remove_command_file(command)

    def commands(self):
        return {"delete device": self._delete}

    def _delete(self, command):
        self.deleted.append(command.target_device)
        self.respond(command, "Completed")
        self.remove_command_file(command)


def write_command(dir_path, payload, name):
    path = dir_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        ProxyAgentPlugin()


def test_refresh_writes_report_and_acks(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    cmd = write_command(
        tmp_path,
        {"commandName": "refresh", "outputDirectory": str(reports)},
        "Refresh-all.command",
    )
    plugin = DemoPlugin()
    plugin.process_command_dir(tmp_path)

    assert len(plugin.refreshed) == 1
    assert (reports / "dev1.report").is_file()
    assert not cmd.exists()  # acknowledged


def test_supported_action_dispatches(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    write_command(
        tmp_path,
        {
            "commandName": "delete device",
            "outputDirectory": str(results),
            "targetDevice": "dev1",
            "commandID": "99",
        },
        "cmd.command",
    )
    plugin = DemoPlugin()
    plugin.process_command_dir(tmp_path)

    assert plugin.deleted == ["dev1"]
    result_files = list(results.glob("*.json"))
    assert len(result_files) == 1
    payload = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert payload == [{"CommandID": "99", "DeviceID": "dev1", "Result": "Completed"}]
    # naming: <commandID>-<PID>-<seq>.json
    assert result_files[0].name.startswith(f"99-{os.getpid()}-")


def test_unsupported_action_reports_error(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    cmd = write_command(
        tmp_path,
        {
            "commandName": "reboot",
            "outputDirectory": str(results),
            "targetDevice": "dev1",
            "commandID": "7",
        },
        "cmd.command",
    )
    plugin = DemoPlugin()
    plugin.process_command_dir(tmp_path)

    payload = json.loads(next(results.glob("*.json")).read_text(encoding="utf-8"))
    assert payload[0]["Result"] == "Error"
    assert not cmd.exists()


def test_plugin_without_commands_rejects_all_actions(tmp_path):
    class Bare(ProxyAgentPlugin):
        def handle_refresh(self, command):
            self.remove_command_file(command)

    results = tmp_path / "results"
    results.mkdir()
    write_command(
        tmp_path,
        {
            "commandName": "anything",
            "outputDirectory": str(results),
            "targetDevice": "d",
            "commandID": "1",
        },
        "cmd.command",
    )
    Bare().process_command_dir(tmp_path)
    assert json.loads(next(results.glob("*.json")).read_text())[0]["Result"] == "Error"


def test_respond_defaults_device_to_target(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    cmd = Command(
        tmp_path / "x.command",
        {
            "commandName": "delete device",
            "outputDirectory": str(results),
            "targetDevice": "target-dev",
            "commandID": "3",
        },
    )
    plugin = DemoPlugin()
    plugin.respond(cmd, "Failed")
    payload = json.loads(next(results.glob("*.json")).read_text())
    assert payload == [
        {"CommandID": "3", "DeviceID": "target-dev", "Result": "Failed"}
    ]
    # explicit override wins
    plugin.respond(cmd, "Completed", device_id="other")
    assert any(
        json.loads(p.read_text())[0]["DeviceID"] == "other"
        for p in results.glob("*.json")
    )


def test_ignores_non_command_files(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (tmp_path / "Thumbs.db").write_text("junk")
    (tmp_path / "subdir").mkdir()
    write_command(
        tmp_path,
        {"commandName": "refresh", "outputDirectory": str(reports)},
        "r.command",
    )
    plugin = DemoPlugin()
    plugin.process_command_dir(tmp_path)
    assert len(plugin.refreshed) == 1


def test_skips_malformed_command_file(tmp_path):
    (tmp_path / "bad.command").write_text("{not json", encoding="utf-8")
    plugin = DemoPlugin()
    plugin.process_command_dir(tmp_path)  # must not raise
    assert plugin.refreshed == []


def test_missing_command_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        DemoPlugin().process_command_dir(tmp_path / "nope")


def test_remove_command_file_missing_is_ignored(tmp_path):
    cmd = Command(
        tmp_path / "gone.command",
        {"commandName": "refresh", "outputDirectory": str(tmp_path)},
    )
    DemoPlugin().remove_command_file(cmd)  # file never existed: no raise


def test_remove_command_file_oserror_logged(tmp_path, monkeypatch):
    cmd = Command(
        tmp_path / "x.command",
        {"commandName": "refresh", "outputDirectory": str(tmp_path)},
    )

    def boom(_):
        raise OSError("permission denied")

    monkeypatch.setattr(os, "remove", boom)
    DemoPlugin().remove_command_file(cmd)  # swallowed
