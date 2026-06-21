"""Friendly recurrence editor <-> cron expression bijection.

The list-view-cockpit successor (Scheduled Tasks) needs a UI editor that
expresses the common 95% case ("weekdays at 9 AM", "Monday + Wednesday at
14:30") with checkboxes + a time picker, while still allowing power users
to drop into a raw cron string.

This module provides:
    - recurrence_to_cron(days, hh_mm)            -> 5-field cron string
    - compute_next_fire_in_tz(cron, tz, base)    -> UTC datetime
    - next_n_fires_in_tz(cron, tz, n, base)      -> list[UTC datetime]
    - human_summary(days, time, tz)              -> "Weekdays at 9:00 AM SGT"
    - validate_cron(cron)                        -> bool

We deliberately use ISO weekday numbering (Mon=0..Sun=6) in the API and
convert at the cron boundary (cron uses Sun=0..Sat=6).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from croniter import croniter  # type: ignore[import-untyped]

# ISO weekday → cron weekday. ISO uses Mon=0..Sun=6; cron uses Sun=0..Sat=6.
_ISO_TO_CRON = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}

ISO_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def recurrence_to_cron(days: list[int], hh_mm: str) -> str:
    """Build a canonical 5-field cron string from days + HH:MM.

    Args:
        days: ISO weekday numbers (Mon=0..Sun=6). Must be non-empty.
        hh_mm: Wall-clock time as "HH:MM" (24-hour).

    Returns:
        A 5-field cron expression: ``minute hour * * dow_csv``.

    Raises:
        ValueError: empty ``days``, malformed ``hh_mm``, or out-of-range numbers.
    """
    if not days:
        raise ValueError("recurrence_to_cron requires at least one day")
    for d in days:
        if d not in _ISO_TO_CRON:
            raise ValueError(f"day {d!r} is out of range (expected 0..6, ISO Mon=0..Sun=6)")
    try:
        hour_s, min_s = hh_mm.split(":")
        hour = int(hour_s)
        minute = int(min_s)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"hh_mm must be 'HH:MM', got {hh_mm!r}") from e
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"hh_mm out of range: {hh_mm!r}")

    # Sort to keep cron canonical, then map ISO -> cron weekdays.
    cron_days = sorted({_ISO_TO_CRON[d] for d in days})
    dow_csv = ",".join(str(d) for d in cron_days)
    return f"{minute} {hour} * * {dow_csv}"


def validate_cron(cron: str) -> bool:
    """Return True iff ``cron`` parses as a valid cron expression."""
    try:
        croniter(cron)
        return True
    except Exception:
        return False


def compute_next_fire_in_tz(
    cron: str,
    tz: str,
    base: datetime | None = None,
) -> datetime:
    """Compute the next fire time in UTC, evaluated against ``tz``'s wall clock.

    Why this matters: a user in Singapore says "fire at 9 AM weekdays". We
    want exactly 09:00 SGT every weekday, not 09:00 UTC. zoneinfo handles DST
    too, so a US-Pacific schedule moves with daylight saving rather than
    drifting.

    Args:
        cron: 5-field cron expression.
        tz: IANA timezone name (e.g. "Asia/Singapore"). Falls back to UTC if
            the name is unknown.
        base: Reference time (UTC). Defaults to "now" UTC.

    Returns:
        The next fire moment as a UTC ``datetime``.
    """
    if base is None:
        base = datetime.now(ZoneInfo("UTC"))
    elif base.tzinfo is None:
        base = base.replace(tzinfo=ZoneInfo("UTC"))

    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")

    base_local = base.astimezone(zone)
    itr = croniter(cron, base_local)
    next_local: datetime = itr.get_next(datetime)
    if next_local.tzinfo is None:
        next_local = next_local.replace(tzinfo=zone)
    result: datetime = next_local.astimezone(ZoneInfo("UTC"))
    return result


def next_n_fires_in_tz(
    cron: str,
    tz: str,
    n: int = 5,
    base: datetime | None = None,
) -> list[datetime]:
    """Return the next ``n`` fire times as UTC datetimes."""
    fires: list[datetime] = []
    cursor = base or datetime.now(ZoneInfo("UTC"))
    for _ in range(n):
        nxt = compute_next_fire_in_tz(cron, tz, cursor)
        fires.append(nxt)
        # Advance one second past the last fire so croniter doesn't return it again.
        cursor = nxt + timedelta(seconds=1)
    return fires


def human_summary(days: list[int] | None, time: str | None, tz: str) -> str:
    """Render a friendly one-liner like "Weekdays at 9:00 AM (SGT)".

    Falls back to a generic phrase when ``days`` / ``time`` are missing
    (i.e. the schedule is on raw cron). Caller can substitute the cron
    string in that case.
    """
    if not days or not time:
        return f"Custom schedule ({tz})"
    days_sorted = sorted(set(days))
    if days_sorted == EVERY_DAY:
        days_phrase = "Every day"
    elif days_sorted == WEEKDAYS:
        days_phrase = "Weekdays"
    elif days_sorted == WEEKENDS:
        days_phrase = "Weekends"
    else:
        days_phrase = ", ".join(ISO_DAY_NAMES[d] for d in days_sorted)
    return f"{days_phrase} at {_format_time_12h(time)} ({tz})"


def _format_time_12h(hh_mm: str) -> str:
    """Render "14:30" as "2:30 PM" — friendlier than 24h for the summary."""
    h_s, m_s = hh_mm.split(":")
    h = int(h_s)
    m = int(m_s)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"
