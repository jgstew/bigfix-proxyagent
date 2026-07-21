import sys
import tomllib

import pytest

from bigfix_proxyagent import config
from bigfix_proxyagent.command import Command
from bigfix_proxyagent.config import (
    ConfigError,
    Field,
    Settings,
    apply_set_command,
    clear_toml_option,
    parse_bool,
    parse_nonempty_str,
    parse_positive_float,
    parse_positive_int,
    parse_regex,
    set_toml_option,
    toml_literal,
    write_validated_toml,
)

tomlkit = pytest.importorskip("tomlkit")


# --- parsers -----------------------------------------------------------------


def test_parse_positive_int():
    assert parse_positive_int("5") == 5
    assert parse_positive_int(" 12 ") == 12
    assert parse_positive_int("0") is None
    assert parse_positive_int("-1") is None
    assert parse_positive_int("x") is None


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


# --- toml_literal ------------------------------------------------------------


def test_toml_literal():
    assert toml_literal(True) == "true"
    assert toml_literal(False) == "false"
    assert toml_literal(5) == "5"
    assert toml_literal(2.5) == "2.5"
    # a single backslash is doubled; a quote is backslash-escaped
    assert toml_literal("a\\d") == '"a\\\\d"'
    assert toml_literal('say "hi"') == '"say \\"hi\\""'
    # and the rendered literal parses back to the original string
    assert tomllib.loads(f"x = {toml_literal(chr(92) + 'd+')}")["x"] == "\\d+"


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


# --- TOML editing: run every case under BOTH backends ------------------------


@pytest.fixture(params=["tomlkit", "regex"])
def backend(request, monkeypatch):
    if request.param == "regex":
        monkeypatch.setattr(config, "_load_tomlkit", lambda: None)
    return request.param


def load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def test_set_top_level_new_and_replace(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    set_toml_option(path, "b", 2)
    assert load(path) == {"a": 1, "b": 2}
    set_toml_option(path, "a", 9)
    assert load(path)["a"] == 9


def test_set_in_existing_table_preserves_comments(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("# keep me\n[settings]\ntimeout = 1  # inline\n")
    set_toml_option(path, "timeout", 5, table="settings")
    set_toml_option(path, "retries", 3, table="settings")
    data = load(path)
    assert data["settings"] == {"timeout": 5, "retries": 3}
    assert "# keep me" in path.read_text()


def test_set_creates_missing_table(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    set_toml_option(path, "level", "high", table="settings")
    assert load(path)["settings"]["level"] == "high"


def test_set_string_value_roundtrips_regex_safe(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[settings]\n")
    set_toml_option(path, "match", r"\d+", table="settings")
    assert load(path)["settings"]["match"] == r"\d+"


def test_clear_top_level_and_from_table(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\nb = 2\n[settings]\nx = 1\ny = 2\n")
    clear_toml_option(path, "a")
    clear_toml_option(path, "x", table="settings")
    data = load(path)
    assert "a" not in data
    assert data["b"] == 2
    assert data["settings"] == {"y": 2}


def test_clear_absent_key_is_noop(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[settings]\nx = 1\n")
    clear_toml_option(path, "missing", table="settings")
    assert load(path)["settings"] == {"x": 1}


def test_clear_missing_table_is_noop(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    clear_toml_option(path, "x", table="settings")
    assert load(path) == {"a": 1}


def test_set_validate_callback_rejects(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")

    def validate(parsed):
        raise ConfigError("nope")

    with pytest.raises(ConfigError, match="nope"):
        set_toml_option(path, "b", 2, validate=validate)
    assert load(path) == {"a": 1}  # unchanged


def test_set_into_empty_file(backend, tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("")
    set_toml_option(path, "first", "value", table="settings")
    assert load(path)["settings"]["first"] == "value"


# --- editing error paths -----------------------------------------------------


def test_tomlkit_backend_rejects_unparseable_file(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("a = = 1\n")  # invalid TOML
    with pytest.raises(ConfigError, match="invalid TOML"):
        set_toml_option(path, "b", 2)


def test_read_error_tomlkit_backend(tmp_path, monkeypatch):
    import pathlib

    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")

    def boom(self, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    with pytest.raises(ConfigError, match="cannot read"):
        set_toml_option(path, "b", 2)


def test_read_error_regex_backend(tmp_path, monkeypatch):
    import pathlib

    path = tmp_path / "c.toml"
    path.write_text("a = 1\n")
    monkeypatch.setattr(config, "_load_tomlkit", lambda: None)

    def boom(self, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    with pytest.raises(ConfigError, match="cannot read"):
        set_toml_option(path, "b", 2)
