import hashlib

from bigfix_proxyagent.device import stable_device_id


def test_stable_device_id_matches_sha256():
    assert stable_device_id("https://example.com") == (
        hashlib.sha256(b"https://example.com").hexdigest()
    )


def test_stable_device_id_is_deterministic():
    assert stable_device_id("key") == stable_device_id("key")


def test_distinct_keys_distinct_ids():
    assert stable_device_id("a") != stable_device_id("b")
