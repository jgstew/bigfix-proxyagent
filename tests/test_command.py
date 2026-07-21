import json

import pytest

from bigfix_proxyagent.command import Command, CommandError


def write_command(tmp_path, payload, name="0001.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_refresh_command(tmp_path):
    path = write_command(
        tmp_path, {"CommandName": "Refresh", "OutputDirectory": "C:\\Reports"}
    )
    command = Command.load(path)
    assert command.is_refresh
    assert str(command.output_directory) == "C:\\Reports"
    assert command.target_device == ""


def test_keys_are_case_insensitive(tmp_path):
    path = write_command(
        tmp_path,
        {
            "commandname": "locate",
            "OUTPUTDIRECTORY": "/tmp/out",
            "targetDevice": "abc123",
            "CommandID": "42",
        },
    )
    command = Command.load(path)
    assert command.name == "locate"
    assert command.target_device == "abc123"
    assert command.command_id == "42"


def test_proxy_agent_10_refresh_fields(tmp_path):
    path = write_command(
        tmp_path,
        {
            "outputDirectory": "C:\\Reports",
            "targetDevice": "abc123",
            "commandName": "refresh",
            "requiredProperties": ["url", "http response code"],
            "deviceReportSequence": 2,
        },
        name="Refresh-abc123.command",
    )
    command = Command.load(path)
    assert command.is_refresh
    assert command.required_properties == ["url", "http response code"]
    assert command.device_report_sequence == 2


def test_action_fields(tmp_path):
    path = write_command(
        tmp_path,
        {
            "CommandName": "push link",
            "OutputDirectory": "/tmp/out",
            "TargetDevice": "abc123",
            "CommandID": "7",
            "targetHint": "https://example.com",
            "commandArguments": "https://new.example.com",
        },
    )
    command = Command.load(path)
    assert not command.is_refresh
    assert command.target_hint == "https://example.com"
    assert command.command_arguments == "https://new.example.com"


def test_missing_optional_fields_default(tmp_path):
    path = write_command(
        tmp_path, {"CommandName": "refresh", "OutputDirectory": "/tmp/out"}
    )
    command = Command.load(path)
    assert command.required_properties == []
    assert command.device_report_sequence is None
    assert command.command_arguments == ""
    assert command.target_hint == ""


def test_non_refresh_requires_target_and_id(tmp_path):
    path = write_command(
        tmp_path, {"CommandName": "locate", "OutputDirectory": "/tmp/out"}
    )
    with pytest.raises(CommandError, match="targetdevice, commandid"):
        Command.load(path)


def test_missing_output_directory(tmp_path):
    path = write_command(tmp_path, {"CommandName": "refresh"})
    with pytest.raises(CommandError, match="outputdirectory"):
        Command.load(path)


def test_missing_command_name(tmp_path):
    path = write_command(tmp_path, {"OutputDirectory": "/tmp/out"})
    with pytest.raises(CommandError, match="commandname"):
        Command.load(path)


def test_empty_required_value_counts_as_missing(tmp_path):
    # An empty targetDevice would make every downstream lookup fail silently,
    # so it must be rejected like an absent key.
    path = write_command(
        tmp_path,
        {
            "CommandName": "locate",
            "OutputDirectory": "/tmp/out",
            "TargetDevice": "",
            "CommandID": "1",
        },
    )
    with pytest.raises(CommandError, match="targetdevice"):
        Command.load(path)


def test_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CommandError, match="cannot read"):
        Command.load(path)


def test_non_object_json(tmp_path):
    path = write_command(tmp_path, ["not", "an", "object"])
    with pytest.raises(CommandError, match="JSON object"):
        Command.load(path)


def test_device_report_sequence_rejects_bool(tmp_path):
    # JSON has no bool/int distinction to the naked eye; a stray true/false
    # must not be treated as a sequence number (bool is a subclass of int).
    path = write_command(
        tmp_path,
        {
            "CommandName": "refresh",
            "OutputDirectory": "/tmp/out",
            "deviceReportSequence": True,
        },
    )
    assert Command.load(path).device_report_sequence is None
