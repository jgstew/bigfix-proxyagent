"""Per-device polling schedule: when a device is due for fresh work.

A polling plugin runs only when the Proxy Agent invokes it (its heartbeat), and
must decide per device whether to do real work now or defer/replay. These are
the pure timing rules that decision rests on, shared so every polling plugin
schedules the same way. All are side-effect-free and take ``now`` explicitly so
they are trivially testable; the plugin passes ``datetime.now().astimezone()``.
"""

from __future__ import annotations

import email.utils
from datetime import datetime

from .util import major_minor

# Fraction of an interval that must elapse before the next poll counts as
# "due". The plugin only runs on the agent's heartbeat, so without slack an
# interval equal to the heartbeat would skip every other beat whenever jitter
# left elapsed time a hair short. 10% absorbs that jitter.
DUE_SLACK = 0.9


def minutes_since(timestamp: str, now: datetime) -> float | None:
    """Minutes between an RFC-2822 ``timestamp`` and ``now``, or None if the
    timestamp cannot be parsed.
    """
    try:
        then = email.utils.parsedate_to_datetime(timestamp)
    except (TypeError, ValueError):
        return None
    return (now - then).total_seconds() / 60


def interval_elapsed(
    last: str | None,
    interval_minutes: float,
    now: datetime,
    *,
    slack: float = DUE_SLACK,
    multiplier: float = 1,
) -> bool:
    """Whether at least ``interval_minutes * multiplier`` (less ``slack``) have
    passed since ``last``.

    Returns True when ``last`` is None (never done) or unparseable, so a
    missing or corrupt timestamp forces the work rather than deferring it
    forever. ``multiplier`` scales the interval (e.g. run a sub-task once every
    N poll intervals).
    """
    if last is None:
        return True
    elapsed = minutes_since(last, now)
    if elapsed is None:
        return True
    return elapsed >= interval_minutes * multiplier * slack


def version_forces_recheck(previous: str | None, current: str | None) -> bool:
    """Whether a major/minor version increase from ``previous`` to ``current``
    should force fresh work, bypassing the interval.

    A minor/major bump can change a report's shape or a check's semantics, so
    the device is re-done immediately instead of replaying a cached result; a
    patch bump does not. Returns False when either version is missing or
    unparseable (no baseline), so the normal interval applies.
    """
    prev = major_minor(previous)
    curr = major_minor(current)
    if prev is None or curr is None:
        return False
    return curr > prev
