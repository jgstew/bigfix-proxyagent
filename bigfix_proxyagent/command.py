"""Parse Proxy Agent command files (JSON with case-insensitive keys)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REFRESH = "refresh"


class CommandError(ValueError):
    """Raised when a command file is unreadable or missing required fields."""


class Command:
    """One JSON command file the Proxy Agent dropped into the command dir.

    Key casing varies (``commandName`` vs ``CommandName``), so all keys are
    compared case-insensitively, mirroring bigfix/trask.
    """

    def __init__(self, location: Path, fields: dict[str, Any]) -> None:
        self.location = location
        self._fields = {key.lower(): value for key, value in fields.items()}

    @classmethod
    def load(cls, location: Path | str) -> Command:
        location = Path(location)
        try:
            with location.open("r", encoding="utf-8") as f:
                fields = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise CommandError(
                f"cannot read command file {location}: {error}"
            ) from error
        if not isinstance(fields, dict):
            raise CommandError(f"command file {location} must contain a JSON object")

        command = cls(location, fields)
        command._validate()
        return command

    def _validate(self) -> None:
        required = ["outputdirectory", "commandname"]
        if not self.is_refresh:
            required += ["targetdevice", "commandid"]
        missing = [key for key in required if not self.get(key)]
        if missing:
            raise CommandError(
                f"command file {self.location} is missing: {', '.join(missing)}"
            )

    def get(self, key: str) -> Any:
        return self._fields.get(key.lower(), "")

    @property
    def name(self) -> str:
        return str(self.get("commandname")).lower()

    @property
    def is_refresh(self) -> bool:
        return self.name == REFRESH

    @property
    def output_directory(self) -> Path:
        return Path(self.get("outputdirectory"))

    @property
    def target_device(self) -> str:
        return str(self.get("targetdevice"))

    @property
    def target_hint(self) -> str:
        """Result of evaluating settings.json's TargetHintRelevance against the
        targeted device; provided by the Proxy Agent with each command.
        """
        return str(self.get("targethint"))

    @property
    def command_id(self) -> str:
        return str(self.get("commandid"))

    @property
    def command_arguments(self) -> str:
        """The rest of the actionscript line for an action command (the text
        after the command name); empty for a refresh.
        """
        return str(self.get("commandarguments"))

    @property
    def required_properties(self) -> list[str]:
        """Properties the Proxy Agent wants refreshed (advisory; a plugin may
        always report everything it knows).
        """
        value = self.get("requiredproperties")
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    @property
    def device_report_sequence(self) -> int | None:
        """Report sequence number the Proxy Agent attached to a refresh
        (seen from Proxy Agent 10.x; echoed back in the device report).
        """
        value = self.get("devicereportsequence")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None
