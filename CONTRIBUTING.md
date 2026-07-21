# Contributing

This is the SDK extracted from
[bigfix-proxyagent-servermon](https://github.com/jgstew/bigfix-proxyagent-servermon).
servermon remains the canonical, end-to-end example: it is the first (and
reference) consumer of this SDK, and the full Proxy Agent protocol reference
lives there (`bigfix/reference-files/ProxyAgents.md`). Read that before
changing anything protocol-shaped here.

## Setup and checks

Python 3.11+ (the `>=3.11` floor is deliberate - keep it even though the
current code also runs on older versions).

```bash
python -m pip install pytest pytest-cov tomlkit
pytest                 # full suite; fast, no network
```

`tomlkit` is a *test* dependency here so both TOML-editing backends (tomlkit
and the regex fallback) are exercised; at runtime it is an optional extra a
consuming plugin may vendor. Tooling config (flake8, pylint, codespell, pytest)
lives in `pyproject.toml`; `pythonpath = ["."]` is why `import bigfix_proxyagent`
works from the repo root without installing.

There is no CI; run the checks by hand.

## Where things live

| Module | Responsibility |
|---|---|
| `plugin.py` | `ProxyAgentPlugin` command-loop base class |
| `command.py` | Parse one Proxy Agent command file |
| `report.py` | Build the standard report keys; network/host helpers |
| `state.py` | `DeviceStateStore` cross-run per-device state |
| `config.py` | Value parsers, settable-field registry, `set` dispatcher, TOML editing |
| `device.py` | `stable_device_id` |
| `cli.py` | Argument parser + logging plumbing |
| `vendor.py` | Load a vendored wheel at runtime |
| `util.py` | Atomic writes, `major_minor` |

## Invariants (protocol constraints, not style)

These are extracted from servermon's own invariants; they are correctness
requirements for any plugin built on the SDK:

- **A device report fully replaces the device's prior data in BigFix.** Any
  value that must survive a refresh with no fresh data (last error, last
  contact) must be re-sent on every report - persist it in `DeviceStateStore`.
- **A refresh must always answer with a report** for a device the agent still
  knows about, or pending actions hang. Deferred deletion
  (`mark_pending_deletion`) exists for this reason.
- **Command-file removal is the acknowledgement, and stays the handler's last
  step** - so a handler can leave the file in place (raise before removing) to
  force a retry when a write fails. The base loop deliberately does **not**
  auto-remove.
- **Command result files** are `<commandID>-<PID>-<seq>.json`; the SDK names
  them for you in `write_command_result`. Once written, a result belongs to
  the agent - never modify or delete it.
- **`DeviceStateStore` is merge-on-save**: `save()` re-reads and overlays only
  this instance's changes, because the agent may run instances concurrently.
- **`tomllib` is the source of truth** for what will parse; every config write
  goes through `write_validated_toml`.

## Conventions

- **Stdlib-only** at runtime. Anything else is an optional extra a plugin
  vendors, never a hard `[project.dependencies]` entry.
- **Test-driven, kept green at the coverage gate** (`--cov-fail-under=99`).
  Every module has a mirrored `tests/test_<module>.py`.
- Type hints throughout; frozen dataclasses for value objects; a module-level
  `log = logging.getLogger(__name__)`; `max-line-length = 88`.
- Bump `__version__` in `bigfix_proxyagent/__init__.py` when you change the
  code.
