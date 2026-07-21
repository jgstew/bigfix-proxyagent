"""Configuration helpers: value parsers, a settable-field registry, the
``set`` action dispatcher, and safe TOML editing.

BigFix operators drive a plugin's configuration with actionscript ``set``
commands (``set <field> <value>``). This module makes that reusable:

- a plugin declares its config fields with :class:`Field` / :class:`Settings`,
  choosing per field (or entirely) whether it may be set from BigFix;
- :func:`apply_set_command` parses and validates one ``set`` command against
  that declaration and calls back to persist it;
- :func:`set_toml_option` / :func:`clear_toml_option` edit a TOML config file
  in place (comments preserved via tomlkit), and :func:`write_validated_toml`
  refuses to write a file that would not load back.

Two config shapes are covered: a *flat* config (top-level keys or a
``[table]``) via :func:`set_toml_option` / :func:`clear_toml_option`, and an
*array-of-tables* (one ``[[table]]`` per device, keyed by an identity field
like servermon's ``url``) via :func:`set_aot_option`, :func:`clear_aot_option`,
:func:`remove_aot_entry`, and :func:`add_aot_entry`. All edit through tomlkit
so comments survive, and validate before committing. tomlkit is loaded via the
standard plugin precedence (installed, plugin ``vendor/``, else the SDK's
bundled copy), so it is effectively always available; an edit for which it
cannot be loaded raises :class:`ConfigError`. A plugin supplies its own schema
``validate`` callback and, for the array-of-tables editors, the table name and
identity key.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .command import Command
from .util import write_text_atomic

log = logging.getLogger(__name__)

# A parser turns raw actionscript argument text into a typed value, or None if
# the text is not valid for the field.
Parser = Callable[[str], Any]


class ConfigError(ValueError):
    """Raised when configuration is invalid or an edit would corrupt the file."""


# --- bounded per-device settings -----------------------------------------------
#
# Two settings almost every plugin has - how often to refresh a device, and how
# long to wait on the external system - follow the same shape: a per-device
# value overrides a plugin-wide ``[settings]`` value, which overrides a default,
# and the result is clamped to a sane range. :func:`resolve_bounded` is that
# rule; the two wrappers below fix the range and default for each.

# Refresh interval, in minutes.
DEFAULT_REFRESH_INTERVAL_MINUTES = 30
MIN_REFRESH_INTERVAL_MINUTES = 1
MAX_REFRESH_INTERVAL_MINUTES = 10080  # one week

# External-system timeout, in seconds. The floor is deliberately permissive (a
# plugin can enforce a stricter minimum itself); the SDK only rules out
# non-positive/absurd values.
DEFAULT_TIMEOUT_SECONDS = 45
MIN_TIMEOUT_SECONDS = 2
MAX_TIMEOUT_SECONDS = 900  # 15 minutes


def resolve_bounded(
    per_device: float | None,
    settings: float | None,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Resolve a per-device numeric setting with precedence and clamping.

    Precedence: ``per_device`` if set, else ``settings`` if set, else
    ``default``. The chosen value is then bounded: above ``maximum`` -> capped
    to ``maximum``; below ``minimum`` -> falls back to ``default``. So any
    out-of-range value (from config or a BigFix ``set``) is normalized, not
    rejected. The value keeps its own numeric type (int in -> int out).
    """
    if per_device is not None:
        value = per_device
    elif settings is not None:
        value = settings
    else:
        value = default
    if value > maximum:
        return maximum
    if value < minimum:
        return default
    return value


def resolve_refresh_interval(
    per_device: int | None = None,
    settings: int | None = None,
    default: int = DEFAULT_REFRESH_INTERVAL_MINUTES,
) -> int:
    """Resolve a device's effective refresh interval in minutes (precedence
    per-device -> [settings] -> default; bounded to
    [1, 10080], an out-of-range low value falling back to ``default``).
    """
    return int(
        resolve_bounded(
            per_device,
            settings,
            default,
            MIN_REFRESH_INTERVAL_MINUTES,
            MAX_REFRESH_INTERVAL_MINUTES,
        )
    )


def resolve_timeout_seconds(
    per_device: float | None = None,
    settings: float | None = None,
    default: float = DEFAULT_TIMEOUT_SECONDS,
) -> float:
    """Resolve a device's effective external-system timeout in seconds
    (precedence per-device -> [settings] -> default; bounded to [2, 900], an
    out-of-range low value falling back to ``default``).
    """
    return resolve_bounded(
        per_device, settings, default, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
    )


# --- value parsers -------------------------------------------------------------


def parse_int(text: str) -> int | None:
    """Parse any integer (None if not an integer). Range is not enforced here -
    e.g. a refresh interval is bounded later by :func:`resolve_refresh_interval`.
    """
    try:
        return int(text.strip())
    except (AttributeError, ValueError):
        return None


def parse_float(text: str) -> float | None:
    """Parse any float (None if not a number). Range is not enforced here -
    e.g. a timeout is bounded later by :func:`resolve_timeout_seconds`.
    """
    try:
        return float(text.strip())
    except (AttributeError, ValueError):
        return None


def parse_positive_int(text: str) -> int | None:
    try:
        value = int(text.strip())
    except (AttributeError, ValueError):
        return None
    return value if value >= 1 else None


def parse_positive_float(text: str) -> float | None:
    try:
        value = float(text.strip())
    except (AttributeError, ValueError):
        return None
    return value if value > 0 else None


def parse_bool(text: str) -> bool | None:
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    return None


def parse_regex(text: str) -> str | None:
    """Return the (stripped) text if it is a valid regular expression, else None."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        re.compile(stripped)
    except re.error:
        return None
    return stripped


def parse_nonempty_str(text: str) -> str | None:
    stripped = text.strip()
    return stripped or None


# --- settable-field registry ---------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One declared config field.

    ``parser`` validates/coerces the raw ``set`` argument text. ``default`` is
    the value restored when the field is *cleared* (``set <field>`` with no
    value). ``settable`` is whether BigFix ``set`` actions may change it -
    set it False to require editing the config file directly for this field.
    """

    parser: Parser
    default: Any = None
    settable: bool = True


class Settings:
    """A plugin's declared config fields, and the policy for setting them.

    Every declared field is settable by default; pass ``Field(..., settable=
    False)`` to disallow a specific field, or simply omit a field to reject it
    entirely.
    """

    def __init__(self, fields: dict[str, Field]) -> None:
        self._fields = dict(fields)

    def __contains__(self, name: str) -> bool:
        return name in self._fields

    def names(self) -> list[str]:
        return list(self._fields)

    def is_settable(self, name: str) -> bool:
        field = self._fields.get(name)
        return field is not None and field.settable

    def default(self, name: str) -> Any:
        return self._fields[name].default

    def parse(self, name: str, raw: str) -> Any:
        """Parse ``raw`` for field ``name`` (None if invalid)."""
        return self._fields[name].parser(raw)


def apply_set_command(
    command: Command,
    settings: Settings,
    apply_setting: Callable[[str, Any, bool], None],
) -> str:
    """Handle one ``set <field> <value>`` command.

    Parses the field and value from ``command.command_arguments``, validates
    against ``settings``, and calls ``apply_setting(field, value, clearing)``
    to persist it (``clearing`` is True when no value was given, so the field
    reverts to its default). ``apply_setting`` should raise :class:`ConfigError`
    if the change cannot be persisted.

    Returns the actionscript command Result string: ``"Completed"`` on success,
    ``"Error"`` for an unknown/disallowed field, an invalid value, or a failed
    persist. The caller writes the result file and removes the command file.
    """
    field, _, raw = str(command.command_arguments).strip().partition(" ")
    field = field.lower()
    raw = raw.strip()

    if field not in settings:
        log.warning("set: unknown field %r", field)
        return "Error"
    if not settings.is_settable(field):
        log.warning("set: field %r is not settable from BigFix", field)
        return "Error"

    clearing = raw == ""
    if clearing:
        value = settings.default(field)
    else:
        value = settings.parse(field, raw)
        if value is None:
            log.warning("set %s: invalid value %r", field, raw)
            return "Error"

    try:
        apply_setting(field, value, clearing)
    except ConfigError as error:
        log.warning("set %s failed: %s", field, error)
        return "Error"
    if clearing:
        log.info("set: cleared %s", field)
    else:
        log.info("set: %s = %r", field, value)
    return "Completed"


# --- TOML editing --------------------------------------------------------------
#
# Editing goes through tomlkit (so comments and formatting survive), loaded via
# the standard plugin precedence - an installed tomlkit, a wheel in the
# plugin's registered ``vendor/``, else the copy bundled inside the SDK. tomlkit
# is therefore effectively always available; an edit for which it cannot be
# loaded (a corrupt/incompatible bundled wheel) fails explicitly with
# :class:`ConfigError` rather than silently degrading.


def write_validated_toml(
    path: Path | str,
    text: str,
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Atomically write ``text`` to ``path`` only if it parses as TOML (and
    passes an optional ``validate(parsed)`` schema check).

    ``tomllib`` is the source of truth for what will actually load. Raises
    :class:`ConfigError` and leaves the file unchanged if the text would not
    parse or ``validate`` rejects it.
    """
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"edit would corrupt {path}: {error}") from error
    if validate is not None:
        validate(parsed)  # may raise ConfigError
    write_text_atomic(Path(path), text)


def set_toml_option(
    path: Path | str,
    key: str,
    value: object,
    *,
    table: str | None = None,
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Set ``key = value`` in a TOML file, preserving comments and formatting.

    ``table`` selects a top-level ``[table]`` (created if missing); ``None``
    means a top-level key. The result is validated by
    :func:`write_validated_toml` before it is committed. Raises
    :class:`ConfigError` if tomlkit cannot be loaded.
    """
    path = Path(path)
    tomlkit = _require_tomlkit(path)
    doc = _load_tomlkit_doc(path, tomlkit)
    target = doc
    if table is not None:
        if table not in doc:
            doc[table] = tomlkit.table()
        target = doc[table]
    target[key] = value
    write_validated_toml(path, tomlkit.dumps(doc), validate)


def clear_toml_option(
    path: Path | str,
    key: str,
    *,
    table: str | None = None,
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Remove ``key`` from a TOML file (top-level or from ``[table]``).

    A no-op if the key (or table) is already absent. Preserves comments and
    formatting; validated before commit. Raises :class:`ConfigError` if tomlkit
    cannot be loaded.
    """
    path = Path(path)
    tomlkit = _require_tomlkit(path)
    doc = _load_tomlkit_doc(path, tomlkit)
    if table is None:
        doc.pop(key, None)
    elif table in doc:
        doc[table].pop(key, None)
    write_validated_toml(path, tomlkit.dumps(doc), validate)


def _load_tomlkit():
    """Return the tomlkit module if available, else None.

    Uses the standard plugin precedence (:func:`vendor.load_wheel_or_bundled`):
    an installed tomlkit, then a loose wheel in the plugin's registered
    ``vendor/`` (see :func:`vendor.set_plugin_vendor_dir`), then the copy
    bundled inside the SDK. Only returns None if even the bundled wheel cannot
    be loaded (corrupt or incompatible), in which case the editors raise via
    :func:`_require_tomlkit`.
    """
    from . import vendor

    return vendor.load_wheel_or_bundled("tomlkit")


def _require_tomlkit(path: Path):
    """The tomlkit module, or :class:`ConfigError` if it cannot be loaded."""
    tomlkit = _load_tomlkit()
    if tomlkit is None:
        raise ConfigError(f"cannot edit {path}: tomlkit is unavailable")
    return tomlkit


def _load_tomlkit_doc(path: Path, tomlkit):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    try:
        return tomlkit.parse(text)
    except Exception as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error


# --- array-of-tables editing ---------------------------------------------------
#
# A plugin whose config is an array-of-tables (one ``[[table]]`` per device,
# keyed by an identity field - servermon's ``url``, say) edits an entry by
# matching that field's value, via tomlkit so comments survive. When the last
# entry is removed a ``table = []`` placeholder is left behind so a schema that
# requires the key still loads; conversely that placeholder is dropped before a
# new entry is appended, since an empty array cannot hold a ``[[table]]`` entry.
#
# ``match_value`` is a string (device identity keys are strings); a Callable
# ``validate`` runs on the parsed result before the atomic write, so an edit
# that would violate the plugin's schema (a duplicate id, a bad value) raises
# :class:`ConfigError` and leaves the file unchanged.

Validate = Callable[[dict[str, Any]], None]


def set_aot_option(
    path: Path | str,
    table: str,
    match_key: str,
    match_value: str,
    key: str,
    value: object,
    *,
    validate: Validate | None = None,
) -> None:
    """Set ``key = value`` on the ``[[table]]`` entry whose ``match_key`` equals
    ``match_value``, editing the file in place (comments/formatting preserved).

    Raises :class:`ConfigError` if no such entry exists, tomlkit cannot be
    loaded, or the result would not parse (or ``validate`` rejects it), leaving
    the file unchanged.
    """
    path = Path(path)
    tomlkit = _require_tomlkit(path)
    _edit_aot_with_tomlkit(
        path,
        table,
        match_key,
        match_value,
        tomlkit,
        lambda entry: entry.__setitem__(key, value),
        validate,
    )


def clear_aot_option(
    path: Path | str,
    table: str,
    match_key: str,
    match_value: str,
    key: str,
    *,
    validate: Validate | None = None,
) -> None:
    """Remove ``key`` from the matching ``[[table]]`` entry (a no-op if the key
    is already absent), editing in place. Raises :class:`ConfigError` if no
    matching entry exists, tomlkit cannot be loaded, or the result would not
    parse.
    """
    path = Path(path)
    tomlkit = _require_tomlkit(path)
    _edit_aot_with_tomlkit(
        path,
        table,
        match_key,
        match_value,
        tomlkit,
        lambda entry: entry.pop(key, None),
        validate,
    )


def remove_aot_entry(
    path: Path | str,
    table: str,
    match_key: str,
    match_value: str,
    *,
    validate: Validate | None = None,
) -> None:
    """Remove the matching ``[[table]]`` entry from the file (in-place edit).

    If the last entry is removed, ``table = []`` is left behind so the file
    still loads. Raises :class:`ConfigError` if no matching entry exists or
    tomlkit cannot be loaded.
    """
    path = Path(path)
    tomlkit = _require_tomlkit(path)
    _remove_aot_with_tomlkit(path, table, match_key, match_value, tomlkit, validate)


def add_aot_entry(
    path: Path | str,
    table: str,
    entry: dict[str, object],
    *,
    validate: Validate | None = None,
) -> None:
    """Append a new ``[[table]]`` entry with the given ``entry`` fields (in-place
    edit). A leftover ``table = []`` placeholder is replaced by the new entry.

    The result is re-parsed (and ``validate``-checked) before it is committed,
    so a schema violation (e.g. a duplicate identity) raises
    :class:`ConfigError` and leaves the file unchanged. Raises
    :class:`ConfigError` if tomlkit cannot be loaded.
    """
    path = Path(path)
    tomlkit = _require_tomlkit(path)
    _add_aot_with_tomlkit(path, table, entry, tomlkit, validate)


def _edit_aot_with_tomlkit(
    path: Path,
    table: str,
    match_key: str,
    match_value: str,
    tomlkit,
    mutate: Callable[[Any], None],
    validate: Validate | None,
) -> None:
    doc = _load_tomlkit_doc(path, tomlkit)
    for entry in doc.get(table, []):
        if entry.get(match_key) == match_value:
            mutate(entry)
            write_validated_toml(path, tomlkit.dumps(doc), validate)
            return
    raise ConfigError(
        f"{path}: no [[{table}]] entry with {match_key} = {match_value!r} found"
    )


def _remove_aot_with_tomlkit(
    path: Path,
    table: str,
    match_key: str,
    match_value: str,
    tomlkit,
    validate: Validate | None,
) -> None:
    doc = _load_tomlkit_doc(path, tomlkit)
    entries = doc.get(table)
    if entries is not None:
        for i, entry in enumerate(entries):
            if entry.get(match_key) == match_value:
                del entries[i]
                # tomlkit drops the key entirely once the array is empty; leave
                # "table = []" so a schema that requires the key still parses.
                if len(doc.get(table, [])) == 0:
                    doc[table] = []
                write_validated_toml(path, tomlkit.dumps(doc), validate)
                return
    raise ConfigError(
        f"{path}: no [[{table}]] entry with {match_key} = {match_value!r} found"
    )


def _add_aot_with_tomlkit(
    path: Path,
    table: str,
    entry: dict[str, object],
    tomlkit,
    validate: Validate | None,
) -> None:
    doc = _load_tomlkit_doc(path, tomlkit)
    new_table = tomlkit.table()
    for key, value in entry.items():
        new_table[key] = value
    existing = doc.get(table)
    # An empty "table = []" (what a full delete leaves behind) is not an
    # array-of-tables and cannot hold a [[table]] entry, so replace it; a
    # populated array is appended to; a missing key is created fresh.
    if existing is not None and len(existing) > 0:
        existing.append(new_table)
    else:
        aot = tomlkit.aot()
        aot.append(new_table)
        doc[table] = aot
    write_validated_toml(path, tomlkit.dumps(doc), validate)
