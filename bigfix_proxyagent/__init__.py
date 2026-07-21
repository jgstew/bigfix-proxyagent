"""Python SDK for building BigFix Management Extender (Proxy Agent) plugins.

A Proxy Agent plugin is a fire-and-forget executable the ``BESProxyAgent``
service launches with ``--commandDir <dir>``: it processes every JSON command
file it finds, writes device reports and command results into each command's
output directory, deletes the command files to acknowledge them, and exits.

This package factors out the parts of that contract that are identical for
every plugin - command-file parsing, atomic report/result writes, the
merge-on-save device state store, report scaffolding, and the command-loop
base class - so a plugin only has to supply its own device-specific logic.

The protocol reference lives in ``reference/ProxyAgents.md``.
"""

__version__ = "0.1.0"
