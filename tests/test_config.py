import sys
import tomllib

import pytest

from bigfix_proxyagent import config
from bigfix_proxyagent.command import Command
from bigfix_proxyagent.config import (
    DEFAULT_REFRESH_INTERVAL_MINUTES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_REFRESH_INTERVAL_MINUTES,
    MAX_TIMEOUT_SECONDS,
    ConfigError,
    Field,
    Settings,
    add_aot_entry,
    apply_set_command,
    clear_aot_option,
    clear_toml_option,
    parse_bool,
    parse_float,
    parse_int,
    parse_nonempty_str,
    parse_positive_float,
    parse_positive_int,
    parse_regex,
    remove_aot_entry,
    resolve_refresh_interval,
    resolve_timeout_seconds,
    set_aot_option,
    set_toml_option,
    write_validated_toml,
)

tomlkit = pytest.importorskip("tomlkit")


# --- parsers -----------------------------------------------------------------


def test_parse_int_accepts_any_integer():
    assert parse_int("5") == 5
    assert parse_int(" -12 ") == -12
    assert parse_int("0") == 0
    assert parse_int("99999") == 99999
    assert parse_int("x") is None
    assert parse_int("1.5") is None


def test_parse_positive_int():
    assert parse_positive_int("5") == 5
    assert parse_positive_int(" 12 ") == 12
    assert parse_positive_int("0") is None
    assert parse_positive_int("-1") is None
    assert parse_positive_int("x") is None


def test_resolve_refresh_interval_precedence():
    # per-device beats settings beats default
    assert resolve_refresh_interval(15, 45) == 15
    assert resolve_refresh_interval(None, 45) == 45
    assert resolve_refresh_interval(None, None) == DEFAULT_REFRESH_INTERVAL_MINUTES
    assert resolve_refresh_interval(None, None, default=90) == 90


def test_resolve_refresh_interval_caps_high():
    assert resolve_refresh_interval(999999) == MAX_REFRESH_INTERVAL_MINUTES
    assert resolve_refresh_interval(None, 999999) == MAX_REFRESH_INTERVAL_MINUTES
    assert resolve_refresh_interval(MAX_REFRESH_INTERVAL_MINUTES) == (
        MAX_REFRESH_INTERVAL_MINUTES
    )


def test_resolve_refresh_interval_below_min_falls_back_to_default():
    assert resolve_refresh_interval(0) == DEFAULT_REFRESH_INTERVAL_MINUTES
    assert resolve_refresh_interval(-5) == DEFAULT_REFRESH_INTERVAL_MINUTES
    # even though per-device is present, an out-of-range value uses the default
    assert resolve_refresh_interval(0, 45) == DEFAULT_REFRESH_INTERVAL_MINUTES
    assert resolve_refresh_interval(0, None, default=90) == 90


def test_parse_float_accepts_any_number():
    assert parse_float("2.5") == 2.5
    assert parse_float(" -3 ") == -3.0
    assert parse_float("900") == 900.0
    assert parse_float("x") is None


def test_resolve_timeout_seconds():
    # precedence per-device -> settings -> default (45)
    assert resolve_timeout_seconds(10, 30) == 10
    assert resolve_timeout_seconds(None, 30) == 30
    assert resolve_timeout_seconds(None, None) == DEFAULT_TIMEOUT_SECONDS
    # capped at 900
    assert resolve_timeout_seconds(5000) == MAX_TIMEOUT_SECONDS
    # 2s is the (permissive) minimum
    assert resolve_timeout_seconds(2) == 2
    # below the 2s minimum -> default, even when a settings value exists
    assert resolve_timeout_seconds(1) == DEFAULT_TIMEOUT_SECONDS
    assert resolve_timeout_seconds(1, 30) == DEFAULT_TIMEOUT_SECONDS
    # float values keep their type
    assert resolve_timeout_seconds(12.5) == 12.5


def test_parse_positive_float():
    assert parse_positive_float("2.5") == 2.5
    assert parse_positive_float("0") is None
    assert parse_positive_float("nope") is None


def test_parse_bool():
    assert parse_bool("true") is True
    assert parse_bool("FALSE") is False
    assert parse_bool("yes") is None


def test_parse_regex():
    assert parse_regex(r"\d+") == r"\d+"
    assert parse_regex("  ok  ") == "ok"
    assert parse_regex("") is None
    assert parse_regex("(unbalanced") is None


def test_parse_nonempty_str():
    assert parse_nonempty_str("  hi ") == "hi"
    assert parse_nonempty_str("   ") is None


# --- Settings registry -------------------------------------------------------


def make_settings():
    return Settings(
        {
            "timeout_seconds": Field(parse_positive_float, default=30.0),
            "retries": Field(parse_positive_int, default=3),
            "locked": Field(parse_bool, default=False, settable=False),
        }
    )


def test_settings_membership_and_names():
    s = make_settings()
    assert "retries" in s
    assert "missing" not in s
    assert set(s.names()) == {"timeout_seconds", "retries", "locked"}


def test_settings_settable_and_default():
    s = make_settings()
    assert s.is_settable("retries") is True
    assert s.is_settable("locked") is False  # explicitly disallowed
    assert s.is_settable("missing") is False
    assert s.default("timeout_seconds") == 30.0
    assert s.parse("retries", "4") == 4


# --- apply_set_command -------------------------------------------------------


def make_set_command(arguments):
    return Command(
        "x.command",
        {
            "commandName": "set",
            "outputDirectory": "/out",
            "targetDevice": "dev",
            "commandID": "1",
            "commandArguments": arguments,
        },
    )


def test_apply_set_value():
    applied = []
    result = apply_set_command(
        make_set_command("retries 5"),
        make_settings(),
        lambda f, v, clearing: applied.append((f, v, clearing)),
    )
    assert result == "Completed"
    assert applied == [("retries", 5, False)]


def test_apply_set_clear_reverts_to_default():
    applied = []
    result = apply_set_command(
        make_set_command("timeout_seconds"),
        make_settings(),
        lambda f, v, clearing: applied.append((f, v, clearing)),
    )
    assert result == "Completed"
    assert applied == [("timeout_seconds", 30.0, True)]


def test_apply_set_unknown_field():
    result = apply_set_command(
        make_set_command("bogus 1"), make_settings(), lambda *a: None
    )
    assert result == "Error"


def test_apply_set_disallowed_field():
    result = apply_set_command(
        make_set_command("locked true"), make_settings(), lambda *a: None
    )
    assert result == "Error"


def test_apply_set_invalid_value():
    result = apply_set_command(
        make_set_command("retries notanumber"), make_settings(), lambda *a: None
    )
    assert result == "Error"


def test_apply_set_persist_failure():
    def boom(field, value, clearing):
        raise ConfigError("disk full")

    result = apply_set_command(make_set_command("retries 5"), make_settings(), boom)
    assert result == "Error"


# --- write_validated_toml ----------------------------------------------------


def test_write_validated_toml_ok(tmp_path):
    path = tmp_path / "c.toml"
    write_validated_toml(path, "a = 1\n")
    assert path.read_text() == "a = 1\n"


def test_write_validated_toml_rejects_bad_syntax(tmp_path):
    path = tmp_path / "c.toml"
    with pytest.raises(ConfigError, match="would corrupt"):
        write_validated_toml(path, "a = = 1")
    assert not path.exists()


def test_write_validated_toml_runs_validator(tmp_path):
    path = tmp_path / "c.toml"

    def validate(parsed):
        if "required" not in parsed:
            raise ConfigError("missing required")

    with pytest.raises(ConfigError, match="missing required"):
        write_validated_toml(path, "a = 1\n", validate)
    assert not path.exists()


# --- _load_tomlkit -----------------------------------------------------------


def test_load_tomlkit_present():
    assert config._load_tomlkit() is not None


def test_load_tomlkit_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "tomlkit", None)
    assert config._load_tomlkit() is None


# --- TOML editing (tomlkit; comments preserved) ------------------------------


def load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_set_top_level_new_and_replace(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    set_toml_option(path, "b", 2)
    assert load(path) == {"a": 1, "b": 2}
    set_toml_option(path, "a", 9)
    assert load(path)["a"] == 9


def test_set_in_existing_table_preserves_comments(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("# keep me\n[settings]\ntimeout = 1  # inline\n")
    set_toml_option(path, "timeout", 5, table="settings")
    set_toml_option(path, "retries", 3, table="settings")
    data = load(path)
    assert data["settings"] == {"timeout": 5, "retries": 3}
    assert "# keep me" in path.read_text()


def test_set_creates_missing_table(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    set_toml_option(path, "level", "high", table="settings")
    assert load(path)["settings"]["level"] == "high"


def test_set_string_value_roundtrips(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[settings]\n")
    set_toml_option(path, "match", r"\d+", table="settings")
    assert load(path)["settings"]["match"] == r"\d+"


def test_clear_top_level_and_from_table(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\nb = 2\n[settings]\nx = 1\ny = 2\n")
    clear_toml_option(path, "a")
    clear_toml_option(path, "x", table="settings")
    data = load(path)
    assert "a" not in data
    assert data["b"] == 2
    assert data["settings"] == {"y": 2}


def test_clear_absent_key_is_noop(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[settings]\nx = 1\n")
    clear_toml_option(path, "missing", table="settings")
    assert load(path)["settings"] == {"x": 1}


def test_clear_missing_table_is_noop(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    clear_toml_option(path, "x", table="settings")
    assert load(path) == {"a": 1}


def test_set_validate_callback_rejects(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")

    def validate(parsed):
        raise ConfigError("nope")

    with pytest.raises(ConfigError, match="nope"):
        set_toml_option(path, "b", 2, validate=validate)
    assert load(path) == {"a": 1}  # unchanged


def test_set_into_empty_file(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("")
    set_toml_option(path, "first", "value", table="settings")
    assert load(path)["settings"]["first"] == "value"


# --- editing error paths -----------------------------------------------------


def test_rejects_unparseable_file(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = = 1\n")  # invalid TOML
    with pytest.raises(ConfigError, match="invalid TOML"):
        set_toml_option(path, "b", 2)


def test_read_error(tmp_path, monkeypatch):
    import pathlib

    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")

    def boom(self, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    with pytest.raises(ConfigError, match="cannot read"):
        set_toml_option(path, "b", 2)


def test_flat_edit_requires_tomlkit(tmp_path, monkeypatch):
    # tomlkit is bundled in the SDK, so this only happens if the bundled wheel
    # is corrupt/incompatible; editing then fails explicitly rather than crash.
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    monkeypatch.setattr(config, "_load_tomlkit", lambda: None)
    with pytest.raises(ConfigError, match="tomlkit is unavailable"):
        set_toml_option(path, "b", 2)
    with pytest.raises(ConfigError, match="tomlkit is unavailable"):
        clear_toml_option(path, "a")
    assert load(path) == {"a": 1}  # unchanged


# --- array-of-tables editing (tomlkit; comments preserved) -------------------
#
# A generic [[table]] keyed by an identity field (here [[items]] keyed by id),
# to prove the editor is not servermon/url specific.

AOT_CONFIG = """\
# global comment
[[items]]
id = "alpha"  # entry comment
weight = 1

[[items]]
id = "beta"
"""


def test_set_aot_option_inserts(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    set_aot_option(path, "items", "id", "beta", "weight", 7)
    data = load(path)
    assert data["items"][1] == {"id": "beta", "weight": 7}
    assert data["items"][0] == {"id": "alpha", "weight": 1}  # untouched
    text = path.read_text()
    assert "# global comment" in text  # comments preserved
    assert "# entry comment" in text


def test_set_aot_option_replaces(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    set_aot_option(path, "items", "id", "alpha", "weight", 99)
    assert load(path)["items"][0]["weight"] == 99
    assert path.read_text().count("weight") == 1  # replaced, not duplicated


def test_set_aot_option_string_backslash_roundtrips(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    set_aot_option(path, "items", "id", "beta", "pattern", r"\d{3}\s+error")
    assert load(path)["items"][1]["pattern"] == r"\d{3}\s+error"


def test_set_aot_option_unknown_entry(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    with pytest.raises(ConfigError, match=r"no \[\[items\]\] entry"):
        set_aot_option(path, "items", "id", "gamma", "weight", 1)


def test_clear_aot_option_removes(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    clear_aot_option(path, "items", "id", "alpha", "weight")
    assert load(path)["items"][0] == {"id": "alpha"}


def test_clear_aot_option_absent_key_is_noop(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    clear_aot_option(path, "items", "id", "beta", "weight")  # never set
    assert load(path)["items"][1] == {"id": "beta"}


def test_clear_aot_option_unknown_entry(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    with pytest.raises(ConfigError, match=r"no \[\[items\]\] entry"):
        clear_aot_option(path, "items", "id", "gamma", "weight")


def test_remove_aot_entry(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    remove_aot_entry(path, "items", "id", "alpha")
    assert [e["id"] for e in load(path)["items"]] == ["beta"]
    text = path.read_text()
    assert "# global comment" in text  # content outside the block survives
    assert "alpha" not in text


def test_remove_last_aot_entry_leaves_empty_array(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[[items]]\nid = "only"\n')
    remove_aot_entry(path, "items", "id", "only")
    assert load(path) == {"items": []}


def test_remove_aot_entry_unknown(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    with pytest.raises(ConfigError, match=r"no \[\[items\]\] entry"):
        remove_aot_entry(path, "items", "id", "gamma")


def test_add_aot_entry_appends(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    add_aot_entry(path, "items", {"id": "gamma"})
    assert [e["id"] for e in load(path)["items"]] == ["alpha", "beta", "gamma"]
    assert "# global comment" in path.read_text()  # existing content preserved


def test_add_aot_entry_to_empty_array(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("items = []\n")
    add_aot_entry(path, "items", {"id": "first"})
    assert [e["id"] for e in load(path)["items"]] == ["first"]


def test_add_aot_entry_multiple_fields(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("items = []\n")
    add_aot_entry(path, "items", {"id": "x", "weight": 3})
    assert load(path)["items"][0] == {"id": "x", "weight": 3}


def test_aot_edit_runs_validate_and_leaves_file_unchanged(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)

    def validate(parsed):
        raise ConfigError("nope")

    with pytest.raises(ConfigError, match="nope"):
        set_aot_option(path, "items", "id", "alpha", "weight", 5, validate=validate)
    assert load(path)["items"][0]["weight"] == 1  # unchanged


def test_add_aot_entry_validate_rejects_duplicate(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)

    def validate(parsed):
        ids = [e["id"] for e in parsed.get("items", [])]
        if len(ids) != len(set(ids)):
            raise ConfigError("duplicate id")

    with pytest.raises(ConfigError, match="duplicate id"):
        add_aot_entry(path, "items", {"id": "alpha"}, validate=validate)
    assert len(load(path)["items"]) == 2  # unchanged


def test_aot_edit_requires_tomlkit(tmp_path, monkeypatch):
    # tomlkit is bundled in the SDK; if the bundled wheel is unusable, an
    # array-of-tables edit fails explicitly rather than crash.
    path = tmp_path / "c.toml"
    path.write_text(AOT_CONFIG)
    monkeypatch.setattr(config, "_load_tomlkit", lambda: None)
    for call in (
        lambda: set_aot_option(path, "items", "id", "alpha", "weight", 5),
        lambda: clear_aot_option(path, "items", "id", "alpha", "weight"),
        lambda: remove_aot_entry(path, "items", "id", "alpha"),
        lambda: add_aot_entry(path, "items", {"id": "gamma"}),
    ):
        with pytest.raises(ConfigError, match="tomlkit is unavailable"):
            call()
    assert load(path)["items"][0]["weight"] == 1  # unchanged
