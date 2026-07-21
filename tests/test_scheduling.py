import email.utils
from datetime import datetime, timedelta, timezone

import pytest

from bigfix_proxyagent.scheduling import (DUE_SLACK, interval_elapsed,
                                          minutes_since,
                                          version_forces_recheck)

TZ = timezone(timedelta(hours=-4))
NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=TZ)


def rfc(dt):
    return email.utils.format_datetime(dt)


def test_due_slack_default():
    assert DUE_SLACK == 0.9


def test_minutes_since_counts_forward():
    assert minutes_since(rfc(NOW - timedelta(minutes=30)), NOW) == pytest.approx(30)


def test_minutes_since_unparseable_returns_none():
    assert minutes_since("garbage", NOW) is None


def test_interval_elapsed_true_when_never_done():
    assert interval_elapsed(None, 60, NOW) is True


def test_interval_elapsed_true_when_unparseable():
    # A corrupt timestamp forces the work rather than deferring forever.
    assert interval_elapsed("garbage", 60, NOW) is True


def test_interval_elapsed_respects_slack():
    # interval 60 -> threshold 60 * 0.9 = 54 minutes
    assert interval_elapsed(rfc(NOW - timedelta(minutes=54)), 60, NOW) is True
    assert interval_elapsed(rfc(NOW - timedelta(minutes=53)), 60, NOW) is False


def test_interval_elapsed_multiplier():
    # interval 10, multiplier 6 -> threshold 60 * 0.9 = 54 minutes
    assert interval_elapsed(
        rfc(NOW - timedelta(minutes=54)), 10, NOW, multiplier=6
    ) is True
    assert interval_elapsed(
        rfc(NOW - timedelta(minutes=53)), 10, NOW, multiplier=6
    ) is False


def test_interval_elapsed_custom_slack():
    assert interval_elapsed(
        rfc(NOW - timedelta(minutes=60)), 60, NOW, slack=1.0
    ) is True
    assert interval_elapsed(
        rfc(NOW - timedelta(minutes=59)), 60, NOW, slack=1.0
    ) is False


def test_version_forces_recheck_on_minor_or_major_bump():
    assert version_forces_recheck("1.0.0", "1.1.0") is True
    assert version_forces_recheck("1.5.0", "2.0.0") is True


def test_version_forces_recheck_ignores_patch_and_downgrade():
    assert version_forces_recheck("1.1.0", "1.1.9") is False  # patch only
    assert version_forces_recheck("1.2.0", "1.1.0") is False  # downgrade


def test_version_forces_recheck_no_baseline():
    assert version_forces_recheck(None, "1.1.0") is False
    assert version_forces_recheck("1.0.0", "not-a-version") is False
