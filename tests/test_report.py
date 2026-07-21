import socket

from bigfix_proxyagent import report
from bigfix_proxyagent.report import (
    base_report,
    local_host_name,
    network_structure,
    restamp_report,
)


def test_base_report_mandatory_keys_and_defaults():
    r = base_report(
        "abc123",
        "example.com",
        "snmpmon",
        last_server_communication="2026-07-21T00:00:00Z",
    )
    assert r["device id"] == "abc123"
    assert r["data source"] == "snmpmon"
    assert r["computer name"] == "example.com"
    assert r["in proxy agent context"] is True
    assert r["last server communication"] == "2026-07-21T00:00:00Z"
    plugin = r["proxy agent plugin"]
    # name defaults to data source; last report time to last communication.
    assert plugin["name"] == "snmpmon"
    assert plugin["last report time"] == "2026-07-21T00:00:00Z"
    # version/host omitted when not supplied.
    assert "version" not in plugin
    assert "host" not in plugin
    # optional keys absent by default.
    assert "last device report time" not in r
    assert "device report sequence" not in r


def test_base_report_full():
    r = base_report(
        "id",
        "name",
        "snmpmon",
        last_server_communication="T1",
        plugin_name="snmp",
        plugin_version="1.2.3",
        plugin_host="relay01",
        plugin_last_report_time="T0",
        last_device_report_time="T-last",
        sequence=5,
    )
    plugin = r["proxy agent plugin"]
    assert plugin == {
        "name": "snmp",
        "version": "1.2.3",
        "host": "relay01",
        "last report time": "T0",
    }
    assert r["last device report time"] == "T-last"
    assert r["device report sequence"] == 5
    assert r["deviceReportSequence"] == 5


def test_sequence_keys_constant():
    assert report.SEQUENCE_KEYS == (
        "device report sequence",
        "deviceReportSequence",
    )


def test_local_host_name_ok(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "relay01")
    assert local_host_name() == "relay01"


def test_local_host_name_fallback(monkeypatch):
    def boom():
        raise OSError("no hostname")

    monkeypatch.setattr(socket, "gethostname", boom)
    assert local_host_name() == "Unknown"


def test_network_structure_ipv4():
    net = network_structure("93.184.216.34")
    assert net == {
        "ip interfaces": [
            {"address": "93.184.216.34", "loopback": False, "up": True}
        ]
    }
    assert "adapters" not in net


def test_network_structure_loopback():
    net = network_structure("127.0.0.1")
    assert net["ip interfaces"][0]["loopback"] is True


def test_network_structure_ipv6_adds_adapters():
    net = network_structure("2606:2800:220:1:248:1893:25c8:1946")
    assert net["adapters"][0]["ipv6 interfaces"][0]["address"] == (
        "2606:2800:220:1:248:1893:25c8:1946"
    )
    assert net["adapters"][0]["up"] is True


def test_network_structure_unparseable_ip_falls_back():
    # Not a valid IP: no exception, treated as non-loopback; the ":" heuristic
    # decides IPv6-ness.
    net = network_structure("not-an-ip")
    assert net["ip interfaces"][0]["loopback"] is False
    assert "adapters" not in net
    net6 = network_structure("garbage:with:colons")
    assert "adapters" in net6


def test_restamp_report_advances_communication_and_sequence():
    original = {
        "device id": "dev1",
        "http check": {"response code": 299},
        "last server communication": "old",
        "device report sequence": 5,
        "deviceReportSequence": 5,
    }
    out = restamp_report(original, last_server_communication="new", sequence=9)
    assert out is original  # mutated in place, returned for chaining
    assert original["last server communication"] == "new"
    assert original["device report sequence"] == 9
    assert original["deviceReportSequence"] == 9
    assert original["http check"] == {"response code": 299}  # payload untouched


def test_restamp_report_without_sequence_leaves_sequence_keys():
    original = {"device report sequence": 5, "last server communication": "old"}
    restamp_report(original, last_server_communication="new")
    assert original["last server communication"] == "new"
    assert original["device report sequence"] == 5  # untouched when sequence is None
