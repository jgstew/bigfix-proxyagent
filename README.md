# bigfix-proxyagent

A Python SDK for building **BigFix Management Extender (Proxy Agent) plugins**.

A Proxy Agent plugin lets BigFix manage "devices" that cannot run a native BES
Client - a URL, an SNMP device, a cloud instance. The `BESProxyAgent` service
launches the plugin fire-and-forget, handing it JSON *command files*; the
plugin answers with device *reports* and command *results*. This SDK factors
out the parts of that contract that are identical for every plugin, so a new
plugin only writes its device-specific logic.

For the full protocol - how the agent, plugins, and devices fit together, the
command/report/result file formats, inspectors, and the action lifecycle - see
the reference docs in the
[servermon repo](https://github.com/jgstew/bigfix-proxyagent-servermon)
(`bigfix/reference-files/ProxyAgents.md`). **servermon is the canonical,
end-to-end example plugin** this SDK was extracted from; when in doubt, read
how it does something.

## What the SDK gives you

| Module | What it provides |
|---|---|
| `plugin` | `ProxyAgentPlugin` - the command-loop base class (dispatch, report/result writing, ack conventions) |
| `command` | `Command` - parse a Proxy Agent command file (case-insensitive keys) |
| `report` | `base_report(...)` (the mandatory + reserved report keys), `network_structure()`, `local_host_name()` |
| `state` | `DeviceStateStore` - merge-on-save per-device state, report caching, deferred deletion |
| `config` | value parsers, a settable-field registry, the `set <field> <value>` action dispatcher, `resolve_refresh_interval` (per-device / settings / default with clamping), and safe TOML editing |
| `device` | `stable_device_id(key)` - a deterministic device id from a plugin-chosen key |
| `cli` | `build_base_parser()`, `setup_logging()` - the standard entry-point plumbing |
| `vendor` | `load_wheel()` - load a vendored pure-Python dependency (incl. this SDK) at runtime |

## Install

```bash
pip install bigfix-proxyagent
```

A deployed plugin runs straight from its folder under the Management Extender
with no `pip install` step, so a plugin typically **vendors** this SDK (and any
other pure-Python dependency) as a wheel in its own `vendor/` directory and
loads it at startup with `vendor.load_wheel("bigfix-proxyagent", VENDOR_DIR,
import_name="bigfix_proxyagent")` - the same pattern servermon uses for
tomlkit.

## Quickstart

A minimal plugin implements `handle_refresh` and, to accept actionscript
commands, `commands()`:

```python
from bigfix_proxyagent import report
from bigfix_proxyagent.device import stable_device_id
from bigfix_proxyagent.plugin import ProxyAgentPlugin


class MyPlugin(ProxyAgentPlugin):
    def handle_refresh(self, command):
        for device in self.discover():          # your device-specific work
            r = report.base_report(
                stable_device_id(device.key),
                device.name,
                "myplugin",                      # the data source / plugin id
                last_server_communication=device.checked_at,
                plugin_version="1.0.0",
                plugin_host=report.local_host_name(),
            )
            r["my check"] = {"value": device.value}   # your inspector data
            self.write_report(command.output_directory, r)
        self.remove_command_file(command)        # last step: acknowledge

    def commands(self):
        return {"delete device": self._delete}

    def _delete(self, command):
        self.respond(command, "Completed")
        self.remove_command_file(command)
```

### Config set from BigFix actions

Declare your config fields once; every declared field is settable from a
BigFix `set <field> <value>` action unless you opt out (`settable=False`, or
omit the field entirely to reject it):

```python
from bigfix_proxyagent.config import (
    Field, Settings, apply_set_command, parse_positive_int, parse_bool,
    set_toml_option, clear_toml_option,
)

FIELDS = Settings({
    "timeout_seconds": Field(parse_positive_int, default=30),
    "verbose":         Field(parse_bool, default=False),
    "api_key":         Field(parse_positive_int, settable=False),  # file-only
})

def _set(self, command):
    def apply(field, value, clearing):
        if clearing:
            clear_toml_option(self.config_path, field, table="settings")
        else:
            set_toml_option(self.config_path, field, value, table="settings")
    self.respond(command, apply_set_command(command, FIELDS, apply))
    self.remove_command_file(command)
```

Entry point (`build_base_parser` + `setup_logging` do the standard plumbing):

```python
from bigfix_proxyagent import cli

def main(argv=None):
    parser = cli.build_base_parser("myplugin", "My plugin.", version="1.0.0")
    args, _ = parser.parse_known_args(argv)
    cli.setup_logging(args.log_level, args.log_file)
    MyPlugin().process_command_dir(args.command_dir)
    return 0
```

## Requirements

Python 3.11+. Standard-library only (`tomlkit`, used for comment-preserving
config edits, is an optional extra; the config editor falls back to a regex
edit without it).

## License

MIT
