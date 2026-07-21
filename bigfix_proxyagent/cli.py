"""Command-line scaffolding shared by plugin entry points.

The Proxy Agent launches a plugin's executable and appends
``--configOptions "<...>" --commandDir "<dir>"``. :func:`build_base_parser`
gives a plugin those standard arguments (plus the common config/state/log
options); a plugin adds its own (e.g. a ``--check`` self-test) on top.
:func:`setup_logging` wires the console + rotating-file logging every plugin
wants. A minimal ``main`` then parses, sets up logging, builds the plugin, and
calls :meth:`ProxyAgentPlugin.process_command_dir`.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
from pathlib import Path

log = logging.getLogger(__name__)

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def build_base_parser(
    prog: str,
    description: str,
    *,
    version: str | None = None,
    default_config: Path | str | None = None,
    default_state_file: Path | str | None = None,
) -> argparse.ArgumentParser:
    """Return an ArgumentParser pre-populated with the standard plugin options.

    A plugin adds its own arguments to the returned parser, then calls
    ``parser.parse_known_args()`` (tolerating arguments a future Proxy Agent
    may append that this version does not know).
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--commandDir",
        dest="command_dir",
        metavar="DIR",
        help="Proxy Agent command directory to process",
    )
    parser.add_argument(
        "--configOptions",
        dest="config_options",
        default="",
        help="options passed by the Proxy Agent (accepted, ignored)",
    )
    parser.add_argument(
        "--config",
        default=None if default_config is None else str(default_config),
        metavar="FILE",
        help="configuration file",
    )
    parser.add_argument(
        "--state-file",
        default=None if default_state_file is None else str(default_state_file),
        metavar="FILE",
        help="JSON file persisting per-device state across runs",
    )
    parser.add_argument(
        "--log-file",
        metavar="FILE",
        help="log to this file, rotating",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=_LOG_LEVELS,
        help="log verbosity (default: INFO)",
    )
    if version is not None:
        parser.add_argument("--version", action="version", version=version)
    return parser


def setup_logging(
    level: str,
    log_file: str | None = None,
    *,
    default_log_file: Path | str | None = None,
) -> None:
    """Configure root logging: always to stderr, and to a rotating file when a
    path is given (``log_file``, else ``default_log_file``).

    A file that cannot be opened (e.g. no write permission under the service
    account) is downgraded to a warning rather than aborting the run.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    chosen = log_file if log_file else default_log_file
    path = Path(chosen) if chosen else None
    file_error: OSError | None = None
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
                )
            )
        except OSError as error:
            file_error = error
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,  # idempotent: replace any prior configuration
    )
    if file_error is not None:
        log.warning("cannot write log file %s: %s", path, file_error)
