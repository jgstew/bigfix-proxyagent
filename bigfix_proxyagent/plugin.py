"""The command-loop base class every plugin subclasses.

The Proxy Agent launches a plugin fire-and-forget: it drops JSON command files
into a directory, starts the executable pointing at it, and does not wait. The
plugin must process every command file, answer each by writing reports or
results into that command's output directory, delete each command file to
acknowledge it, and exit (see the servermon repo's
``bigfix/reference-files/ProxyAgents.md``).

:class:`ProxyAgentPlugin` owns that loop, the refresh/action dispatch, and the
file-writing conventions (result-file naming, atomic writes). A concrete
plugin implements :meth:`handle_refresh` and, to accept actionscript commands,
overrides :meth:`commands`.
"""

from __future__ import annotations

import abc
import itertools
import logging
import os
from pathlib import Path
from typing import Callable

from .command import Command, CommandError
from .util import write_json_atomic

log = logging.getLogger(__name__)

CommandHandler = Callable[[Command], None]

# Result files are "<commandID>-<PID>-<seq>.json" (the spec-suggested naming) so
# concurrent plugin instances can never collide. One process-wide counter.
_result_seq = itertools.count()


class ProxyAgentPlugin(abc.ABC):
    """Base class for a BigFix Proxy Agent plugin.

    Subclass responsibilities:

    - implement :meth:`handle_refresh` to answer a refresh with device
      report(s) - and, per the protocol, *always* produce a report for a
      device the agent still knows about (even a cached replay), or pending
      actions hang;
    - optionally override :meth:`commands` to map whitelisted actionscript
      command names to handler methods;
    - call :meth:`remove_command_file` as the last step of each handler, once
      its reports/results are safely written - deleting the command file is
      the acknowledgement. Leaving it in place (e.g. by raising before that
      step when a write fails) makes the agent retry on the next invocation.
    """

    def process_command_dir(self, command_dir: Path | str) -> None:
        """Process every command file currently in ``command_dir``."""
        command_dir = Path(command_dir)
        if not command_dir.is_dir():
            raise FileNotFoundError(f"command directory does not exist: {command_dir}")

        for path in sorted(command_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in (".command", ".json"):
                # Stray files (editor temp files, Thumbs.db, ...) are not
                # commands; skip quietly rather than warning every run.
                log.debug("ignoring non-command file %s", path.name)
                continue
            try:
                command = Command.load(path)
            except CommandError as error:
                log.warning("skipping %s: %s", path.name, error)
                continue
            self.process_command(command)

    def process_command(self, command: Command) -> None:
        """Dispatch one command to the right handler."""
        if command.is_refresh:
            self.handle_refresh(command)
            return
        handler = self.commands().get(command.name)
        if handler is not None:
            handler(command)
        else:
            self.handle_unsupported(command)

    @abc.abstractmethod
    def handle_refresh(self, command: Command) -> None:
        """Answer a refresh: write ``<device id>.report`` file(s) into
        ``command.output_directory`` (via :meth:`write_report`), then.

        :meth:`remove_command_file`.

        A refresh carrying a ``command_id`` is action-driven ("check now"):
        its output directory is the action-results directory and it expects a
        command *result*, not device reports.
        """

    def commands(self) -> dict[str, CommandHandler]:
        """Map actionscript command names to handler callables.

        The default supports no actions. Override to return e.g.
        ``{"delete device": self._delete}``. Names must be actionscript
        commands the agent will forward (see the servermon repo's
        ``bigfix/reference-files/ProxyPluginCommands.json``).
        """
        return {}

    def handle_unsupported(self, command: Command) -> None:
        """Reject a command the plugin does not handle with an Error result."""
        self.respond(command, "Error")
        log.warning(
            "unsupported command %r for device %s: reported Error",
            command.name,
            command.target_device,
        )
        self.remove_command_file(command)

    # -- file-writing helpers -------------------------------------------------

    def write_report(self, output_directory: Path | str, report: dict) -> None:
        """Atomically write a device report as ``<device id>.report``."""
        path = Path(output_directory) / f"{report['device id']}.report"
        write_json_atomic(path, report)

    def write_command_result(
        self, command: Command, results: list[dict[str, str]]
    ) -> None:
        """Atomically write an action's result file into its output directory.

        Once placed the file belongs to the agent - never modify or delete it.
        """
        name = f"{command.command_id}-{os.getpid()}-{next(_result_seq)}.json"
        write_json_atomic(command.output_directory / name, results)

    def respond(
        self, command: Command, result: str, device_id: str | None = None
    ) -> None:
        """Write a single-entry command result (the common case).

        ``result`` is ``Completed`` / ``Failed`` / ``Error``; ``device_id``
        defaults to the command's target device.
        """
        self.write_command_result(
            command,
            [
                {
                    "CommandID": command.command_id,
                    "DeviceID": command.target_device if device_id is None else device_id,
                    "Result": result,
                }
            ],
        )

    def remove_command_file(self, command: Command) -> None:
        """Delete a processed command file to acknowledge it to the agent."""
        try:
            os.remove(command.location)
        except FileNotFoundError:
            pass  # some Proxy Agent versions clean up command files themselves
        except OSError as error:
            log.warning(
                "could not remove command file %s: %s", command.location, error
            )
