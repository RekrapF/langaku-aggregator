# logs/services.py
from __future__ import annotations

import datetime as dt
from typing import Dict, List

import pytz
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import LearningRecord


def _to_aware_utc(x: str | dt.datetime) -> dt.datetime:
    """Parse an ISO string or datetime into a tz-aware datetime in UTC."""
    if isinstance(x, str):
        d = parse_datetime(x)
        if d is None:
            raise ValueError("from/to must be ISO-8601")
    elif isinstance(x, dt.datetime):
        d = x
    else:
        raise TypeError("datetime must be str or datetime")
    if timezone.is_naive(d):
        d = timezone.make_aware(d, dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def _floor_local(d: dt.datetime, granularity: str, tz: dt.tzinfo) -> dt.datetime:
    """Floor a datetime to the bucket start at the given granularity in the local timezone (return tz-aware local time)."""
    ld = d.astimezone(tz)
    if granularity == "hour":
        return ld.replace(minute=0, second=0, microsecond=0)
    if granularity == "day":
        return ld.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "month":
        return ld.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("granularity must be hour|day|month")


def _step_local(d: dt.datetime, granularity: str, tz: dt.tzinfo) -> dt.datetime:
    """Advance by one bucket in the local timezone."""
    if granularity == "hour":
        return d + dt.timedelta(hours=1)
    if granularity == "day":
        return d + dt.timedelta(days=1)
    if granularity == "month":
        year = d.year + (1 if d.month == 12 else 0)
        month = 1 if d.month == 12 else d.month + 1
        return d.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("granularity must be hour|day|month")


def _bucket_key_by_end_local(end_at_utc: dt.datetime, granularity: str, tz: dt.tzinfo) -> dt.datetime:
    """Assign a record to a bucket using the local-time view of end_at (return tz-aware local bucket start)."""
    return _floor_local(end_at_utc, granularity, tz)


def _same_local_day(a_utc: dt.datetime, b_utc: dt.datetime, tz: dt.tzinfo) -> bool:
    """Check if two UTC datetimes fall on the same local calendar day (same Y-M-D in the given timezone)."""
    la = a_utc.astimezone(tz)
    lb = b_utc.astimezone(tz)
    return (la.year, la.month, la.day) == (lb.year, lb.month, lb.day)


def _iter_bucket_starts(
    from_utc: dt.datetime, to_utc: dt.datetime, granularity: str, tz: dt.tzinfo
) -> List[dt.datetime]:
    """Generate the list of local bucket starts that cover [from, to) (tz-aware, local timezone, right-open interval)."""
    start_local = _floor_local(from_utc, granularity, tz)
    end_local = _floor_local(to_utc, granularity, tz)  # right-open upper bound
    out: List[dt.datetime] = []
    cur = start_local
    while cur < end_local:
        out.append(cur)
        cur = _step_local(cur, granularity, tz)
    return out


def summarize_with_sma(  # keep the name for compatibility; SMA is no longer returned
    user_id: str,
    dt_from: str | dt.datetime,
    dt_to: str | dt.datetime,
    *,
    granularity: str,
    tz: str,
) -> List[Dict]:
    """
    Aggregate a user's learning word counts and study minutes over [from, to)
    at the specified granularity and timezone.

    Rules:
      1) Word counts are assigned to buckets by the local-time end_at.
      2) Study minutes are counted as (end - start) // 60 only when both ends exist
         and fall on the same local calendar day; otherwise 0.
      3) Return all buckets including empty ones to support strict means when include_empty=true.
    """
    if granularity not in ("hour", "day", "month"):
        raise ValueError("granularity must be hour|day|month")

    tzinfo = pytz.timezone(tz)
    f_utc = _to_aware_utc(dt_from)
    t_utc = _to_aware_utc(dt_to)
    if f_utc >= t_utc:
        return []

    bucket_starts_local = _iter_bucket_starts(f_utc, t_utc, granularity, tzinfo)
    idx: Dict[dt.datetime, int] = {bs: i for i, bs in enumerate(bucket_starts_local)}
    wc = [0.0 for _ in bucket_starts_local]
    mins = [0.0 for _ in bucket_starts_local]

    qs = LearningRecord.objects.filter(
        user_id=user_id,
        end_at__gte=f_utc,
        end_at__lt=t_utc,
    ).only("word_count", "start_at", "end_at")

    for rec in qs:
        end_at = rec.end_at
        if end_at is None:
            continue

        bucket_key = _bucket_key_by_end_local(end_at, granularity, tzinfo)
        if bucket_key not in idx:
            continue
        bi = idx[bucket_key]

        wc[bi] += float(rec.word_count or 0)

        s = rec.start_at
        if s is not None and _same_local_day(s, end_at, tzinfo):
            seconds = max(0, int((end_at - s).total_seconds()))
            mins[bi] += float(seconds // 60)
        else:
            mins[bi] += 0.0

    out = []
    for i, bs_local in enumerate(bucket_starts_local):
        out.append({
            "bucket_start": bs_local.isoformat(),  # local timezone ISO
            "wc_sum": float(wc[i]),
            "mins_sum": float(mins[i]),
        })
    return out
