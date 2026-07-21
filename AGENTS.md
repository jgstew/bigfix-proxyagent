# AGENTS.md

Guardrails for coding agents. Read [README.md](README.md) (what the SDK is and
how a plugin uses it) and [CONTRIBUTING.md](CONTRIBUTING.md) (module map,
invariants, testing) first - this file only states the imperatives.

This SDK was extracted from
[bigfix-proxyagent-servermon](https://github.com/jgstew/bigfix-proxyagent-servermon),
which stays the canonical end-to-end example and holds the protocol reference
(`bigfix/reference-files/ProxyAgents.md`). Consult it for anything
protocol-shaped.

## Test-driven development

Work test-first. Write the failing test before the code, confirm it fails for
the expected reason, then make it pass with the rest of the suite still green.
If a test encoded a wrong assumption, fixing it is allowed - but call out
which test changed and why.

## Definition of done

There is no CI. Before treating a change as complete:

- `pytest` passes (fast, no network) and coverage stays at/above the gate
  (`--cov-fail-under=99`).
- `pre-commit run -a` is clean, if configured.
- Bump `__version__` in `bigfix_proxyagent/__init__.py` when you change code.

## Guardrails

- The **invariants** in CONTRIBUTING are protocol constraints, not style -
  treat them as correctness requirements. In particular: reports fully
  replace prior data, a refresh must always answer, and command-file removal
  is the handler's last (on-success) step.
- Stay **stdlib-only** at runtime. Optional dependencies are vendored by the
  consuming plugin, never added to `[project.dependencies]`.
- This is a library many plugins depend on: treat the public API
  (`ProxyAgentPlugin`, `Command`, `base_report`, `DeviceStateStore`,
  `Settings`/`apply_set_command`, the `cli` helpers) as a contract - changing
  a signature ripples out to every plugin.
