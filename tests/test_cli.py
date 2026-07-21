import logging
import logging.handlers

import pytest

from bigfix_proxyagent import cli


@pytest.fixture
def reset_logging():
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    root.setLevel(saved_level)


def test_base_parser_defaults():
    parser = cli.build_base_parser("demo", "a demo plugin")
    args, unknown = parser.parse_known_args(
        ["--commandDir", "/cmd", "--configOptions", "opts", "--future-flag", "x"]
    )
    assert args.command_dir == "/cmd"
    assert args.config_options == "opts"
    assert args.config is None
    assert args.state_file is None
    assert args.log_level == "INFO"
    assert unknown == ["--future-flag", "x"]  # tolerated


def test_base_parser_with_defaults_and_version(capsys):
    parser = cli.build_base_parser(
        "demo",
        "d",
        version="1.2.3",
        default_config="/etc/demo.toml",
        default_state_file="/var/demo-state.json",
    )
    args, _ = parser.parse_known_args([])
    assert args.config == "/etc/demo.toml"
    assert args.state_file == "/var/demo-state.json"
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])
    assert "1.2.3" in capsys.readouterr().out


def test_setup_logging_stderr_only(reset_logging):
    cli.setup_logging("DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.StreamHandler)


def test_setup_logging_to_default_file(tmp_path, reset_logging):
    log_file = tmp_path / "logs" / "demo.log"
    cli.setup_logging("INFO", default_log_file=log_file)
    root = logging.getLogger()
    assert any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers
    )
    logging.getLogger("demo").info("hello")
    assert log_file.is_file()


def test_setup_logging_explicit_file_overrides_default(tmp_path, reset_logging):
    chosen = tmp_path / "chosen.log"
    cli.setup_logging("INFO", str(chosen), default_log_file=tmp_path / "default.log")
    logging.getLogger("demo").warning("x")
    assert chosen.is_file()
    assert not (tmp_path / "default.log").exists()


def test_setup_logging_file_error_degrades(tmp_path, reset_logging, monkeypatch):
    def boom(*a, **k):
        raise OSError("no permission")

    monkeypatch.setattr(logging.handlers, "RotatingFileHandler", boom)
    cli.setup_logging("INFO", default_log_file=tmp_path / "x.log")
    root = logging.getLogger()
    # Only the stream handler survived; the run was not aborted.
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.StreamHandler)
