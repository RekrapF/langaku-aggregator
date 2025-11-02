# logs/tests/test_api.py
import datetime as dt
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient


def iso(dt_):
    """Ensure tz-aware UTC -> ISO string"""
    if timezone.is_naive(dt_):
        dt_ = timezone.make_aware(dt_, dt.timezone.utc)
    return dt_.astimezone(dt.timezone.utc).isoformat()


@pytest.mark.django_db
def test_idempotent_create_success_and_replay():
    c = APIClient()
    url = "/api/records"
    end_at = timezone.now().replace(microsecond=0)
    payload = {
        "user_id": "u-idem",
        "idempotency_key": "idem-1",
        "word_count": 10,
        "end_at": iso(end_at),
    }

    r1 = c.post(url, payload, format="json")
    assert r1.status_code in (200, 201)
    obj1 = r1.json()
    assert obj1["user_id"] == "u-idem"
    assert obj1["idempotency_key"] == "idem-1"
    assert obj1["word_count"] == 10
    assert obj1["start_at"] is None
    assert obj1["end_at"] is not None

    r2 = c.post(url, payload, format="json")
    assert r2.status_code == 200
    obj2 = r2.json()
    assert obj2["id"] == obj1["id"]


@pytest.mark.django_db
def test_idempotent_conflict_same_key_different_payload_409():
    c = APIClient()
    url = "/api/records"
    now = timezone.now().replace(microsecond=0)
    base_payload = {
        "user_id": "u-idem-conf",
        "idempotency_key": "idem-x",
        "word_count": 5,
        "end_at": iso(now),
    }
    assert c.post(url, base_payload, format="json").status_code in (200, 201)

    conflict_payload = dict(base_payload, word_count=6)
    r_conf = c.post(url, conflict_payload, format="json")
    assert r_conf.status_code == 409
    assert "Idempotency-Key reused" in r_conf.json().get("detail", "")


@pytest.mark.django_db
def test_same_day_duration_minutes_is_difference_in_minutes():
    c = APIClient()
    url = "/api/records"
    start_at = timezone.datetime(2025, 10, 27, 10, 0, 0, tzinfo=dt.timezone.utc)
    end_at = start_at + timedelta(minutes=45)
    r = c.post(
        url,
        {
            "user_id": "u-dur",
            "idempotency_key": "dur-1",
            "word_count": 20,
            "start_at": iso(start_at),
            "end_at": iso(end_at),
        },
        format="json",
    )
    assert r.status_code in (200, 201)
    assert r.json()["study_minutes"] == 45

    qs = (
        "/api/users/u-dur/summary?"
        "from=2025-10-27T00:00:00Z&to=2025-10-28T00:00:00Z"
        "&granularity=day&tz=Asia/Tokyo&include_empty=true"
    )
    g = c.get(qs)
    assert g.status_code == 200
    data = g.json()
    assert data["totals"]["word_count"] == 20
    assert data["totals"]["study_minutes"] == 45
    assert data["averages_per_bucket"]["word_count"] == 20
    assert data["averages_per_bucket"]["study_minutes"] == 45


@pytest.mark.django_db
def test_cross_day_rule_word_count_to_end_day_and_minutes_zero():
    c = APIClient()
    url = "/api/records"

    start_at = timezone.datetime(2025, 10, 27, 14, 30, 0, tzinfo=dt.timezone.utc)
    end_at = timezone.datetime(2025, 10, 27, 15, 30, 0, tzinfo=dt.timezone.utc)

    assert c.post(
        url,
        {
            "user_id": "u-cross",
            "idempotency_key": "cross-1",
            "word_count": 100,
            "start_at": iso(start_at),
            "end_at": iso(end_at),
        },
        format="json",
    ).status_code in (200, 201)

    qs = (
        "/api/users/u-cross/summary?"
        "from=2025-10-27T00:00:00Z&to=2025-10-29T00:00:00Z"
        "&granularity=day&tz=Asia/Tokyo&include_empty=true"
    )
    g = c.get(qs)
    assert g.status_code == 200
    data = g.json()
    """
    Previously validated buckets explicitly:
      - assert there are 2 buckets
      - day 1 wc_sum=0, day 2 wc_sum=100
      - mins_sum = 0 for both days (cross-day -> 0)
    Kept here as reference but not used since response no longer includes 'buckets'.
    """
    assert data["totals"]["word_count"] == 100
    assert data["totals"]["study_minutes"] == "study minutes less than a minute"
    assert data["averages_per_bucket"]["word_count"] == 50.0
    assert data["averages_per_bucket"]["study_minutes"] == "study minutes less than a minute"


@pytest.mark.django_db
def test_include_empty_false_uses_active_bucket_count_as_denominator():
    c = APIClient()
    url = "/api/records"

    base = timezone.datetime(2025, 10, 27, 10, 0, 0, tzinfo=dt.timezone.utc)
    assert c.post(
        url,
        {
            "user_id": "u-active",
            "idempotency_key": "active-1",
            "word_count": 30,
            "start_at": iso(base + timedelta(hours=1, minutes=10)),
            "end_at": iso(base + timedelta(hours=2)),
        },
        format="json",
    ).status_code in (200, 201)

    qs_true = (
        "/api/users/u-active/summary?"
        f"from={iso(base)}&to={iso(base + timedelta(hours=3))}"
        "&granularity=hour&tz=Asia/Tokyo&include_empty=true"
    )
    qs_false = (
        "/api/users/u-active/summary?"
        f"from={iso(base)}&to={iso(base + timedelta(hours=3))}"
        "&granularity=hour&tz=Asia/Tokyo&include_empty=false"
    )

    r_true = c.get(qs_true).json()
    r_false = c.get(qs_false).json()

    # include_empty=true -> denominator = all buckets in window (3 hours)
    assert r_true["averages_per_bucket"]["word_count"] == 30.0 / 3.0
    # include_empty=false -> denominator = active buckets only (1 hour)
    assert r_false["averages_per_bucket"]["word_count"] == 30.0


@pytest.mark.django_db
def test_small_value_replacement_for_totals_and_averages():
    c = APIClient()
    url = "/api/records"
    start_at = timezone.datetime(2025, 10, 27, 9, 0, 0, tzinfo=dt.timezone.utc)
    end_at = start_at + timedelta(seconds=30)

    assert c.post(
        url,
        {
            "user_id": "u-small",
            "idempotency_key": "small-1",
            "word_count": 0,
            "start_at": iso(start_at),
            "end_at": iso(end_at),
        },
        format="json",
    ).status_code in (200, 201)

    qs = (
        "/api/users/u-small/summary?"
        f"from={iso(start_at)}&to={iso(start_at + timedelta(hours=1))}"
        "&granularity=hour&tz=Asia/Tokyo&include_empty=true"
    )
    r = c.get(qs)
    assert r.status_code == 200
    data = r.json()

    # When totals or averages are below thresholds, they are replaced with the specified messages
    assert data["totals"]["word_count"] == "word count less than 1"
    assert data["totals"]["study_minutes"] == "study minutes less than a minute"

    assert data["averages_per_bucket"]["word_count"] == "word count less than 1"
    assert data["averages_per_bucket"]["study_minutes"] == "study minutes less than a minute"


@pytest.mark.django_db
def test_small_value_replacement_for_totals_and_averages():
    c = APIClient()
    url = "/api/records"
    start_at = timezone.datetime(2025, 10, 27, 9, 0, 0, tzinfo=dt.timezone.utc)
    end_at = start_at + timedelta(seconds=30)

    assert c.post(
        url,
        {
            "user_id": "u-small",
            "idempotency_key": "small-1",
            "word_count": 0,
            "start_at": iso(start_at),
            "end_at": iso(end_at),
        },
        format="json",
    ).status_code in (200, 201)

    qs = (
        "/api/users/u-small/summary?"
        f"from={iso(start_at)}&to={iso(start_at + timedelta(hours=1))}"
        "&granularity=hour&tz=Asia/Tokyo&include_empty=true"
    )
    r = c.get(qs)
    assert r.status_code == 200
    data = r.json()

    # Same small-value replacement assertions
    assert data["totals"]["word_count"] == "word count less than 1"
    assert data["totals"]["study_minutes"] == "study minutes less than a minute"

    assert data["averages_per_bucket"]["word_count"] == "word count less than 1"
    assert data["averages_per_bucket"]["study_minutes"] == "study minutes less than a minute"

# === New test case 1 ===
# Same user submits learning records for 5 consecutive days; get hourly and daily averages
def _post_record(c, user_id, key, wc, start_utc, minutes=60):
    url = "/api/records"
    payload = {
        "user_id": user_id,
        "idempotency_key": key,
        "word_count": wc,
        "start_at": iso(start_utc),
        "end_at": iso(start_utc + timedelta(minutes=minutes)),
    }
    r = c.post(url, payload, format="json")
    assert r.status_code in (200, 201)
    return r

@pytest.mark.django_db
def test_consecutive_5_days_hour_and_day_averages():
    c = APIClient()
    uid = "u-5days"
    # 2025-10-01 ~ 2025-10-05, one record per day, 60 minutes each
    base = timezone.datetime(2025, 10, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    wcs = [10, 20, 30, 40, 50]  # different word counts per day
    for i, wc in enumerate(wcs):
        start = base + timedelta(days=i)
        _post_record(c, uid, f"k-{i}", wc, start, minutes=60)

    # Query window covering those 5 days ([from,to) = [10/01, 10/06))
    f = "2025-10-01T00:00:00Z"
    t = "2025-10-06T00:00:00Z"

    # Daily averages (include_empty=true -> denominator = 5 days)
    qs_day = f"/api/users/{uid}/summary?from={f}&to={t}&granularity=day&tz=UTC&include_empty=true"
    r_day = c.get(qs_day)
    assert r_day.status_code == 200
    data_day = r_day.json()
    assert data_day["totals"]["word_count"] == sum(wcs)            # 150
    assert data_day["totals"]["study_minutes"] == 60 * 5           # 300
    assert data_day["averages_per_bucket"]["word_count"] == sum(wcs) / 5.0   # 30
    assert data_day["averages_per_bucket"]["study_minutes"] == 60.0

    # Hourly averages (include_empty=false -> denominator = 5 active hours)
    qs_hour = f"/api/users/{uid}/summary?from={f}&to={t}&granularity=hour&tz=UTC&include_empty=false"
    r_hour = c.get(qs_hour)
    assert r_hour.status_code == 200
    data_hour = r_hour.json()
    assert data_hour["totals"]["word_count"] == sum(wcs)           # 150
    assert data_hour["totals"]["study_minutes"] == 60 * 5          # 300
    assert data_hour["averages_per_bucket"]["word_count"] == sum(wcs) / 5.0   # 30
    assert data_hour["averages_per_bucket"]["study_minutes"] == 60.0


# === New test case 2 ===
# Same user submits once in Feb, Apr, Aug, Sep 2025; get hourly, daily, monthly averages
@pytest.mark.django_db
def test_scattered_months_hour_day_month_averages():
    c = APIClient()
    uid = "u-scatter-4m"
    # Four months, each 60 minutes
    points = [
        (timezone.datetime(2025, 2, 10, 12, 0, 0, tzinfo=dt.timezone.utc), 10),
        (timezone.datetime(2025, 4, 10, 12, 0, 0, tzinfo=dt.timezone.utc), 20),
        (timezone.datetime(2025, 8, 10, 12, 0, 0, tzinfo=dt.timezone.utc), 30),
        (timezone.datetime(2025, 9, 10, 12, 0, 0, tzinfo=dt.timezone.utc), 40),
    ]
    for i, (start, wc) in enumerate(points):
        _post_record(c, uid, f"m-{i}", wc, start, minutes=60)

    f = "2025-02-01T00:00:00Z"
    t = "2025-10-01T00:00:00Z"  # Cover Feb..Sep (8 months)

    total_wc = sum(wc for _, wc in points)      # 100
    total_min = 60 * len(points)                # 240

    # Hourly averages (active hours = 4)
    r_hour = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=hour&tz=UTC&include_empty=false")
    assert r_hour.status_code == 200
    dh = r_hour.json()
    assert dh["totals"]["word_count"] == total_wc
    assert dh["totals"]["study_minutes"] == total_min
    assert dh["averages_per_bucket"]["word_count"] == total_wc / 4.0   # 25
    assert dh["averages_per_bucket"]["study_minutes"] == total_min / 4.0  # 60

    # Daily averages (active days = 4)
    r_day = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=day&tz=UTC&include_empty=false")
    assert r_day.status_code == 200
    dd = r_day.json()
    assert dd["totals"]["word_count"] == total_wc
    assert dd["totals"]["study_minutes"] == total_min
    assert dd["averages_per_bucket"]["word_count"] == total_wc / 4.0
    assert dd["averages_per_bucket"]["study_minutes"] == total_min / 4.0

    # Monthly averages: compare include_empty=true vs false
    # include_empty=true -> denominator = 8 months (Feb..Sep)
    r_month_true = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=month&tz=UTC&include_empty=true")
    assert r_month_true.status_code == 200
    dm_t = r_month_true.json()
    assert dm_t["totals"]["word_count"] == total_wc
    assert dm_t["totals"]["study_minutes"] == total_min
    assert dm_t["averages_per_bucket"]["word_count"] == total_wc / 8.0   # 12.5
    assert dm_t["averages_per_bucket"]["study_minutes"] == total_min / 8.0  # 30

    # include_empty=false -> denominator = 4 months (months with submissions)
    r_month_false = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=month&tz=UTC&include_empty=false")
    assert r_month_false.status_code == 200
    dm_f = r_month_false.json()
    assert dm_f["averages_per_bucket"]["word_count"] == total_wc / 4.0    # 25
    assert dm_f["averages_per_bucket"]["study_minutes"] == total_min / 4.0  # 60


# === New test case 3 ===
# Same user submits on “random” (fixed) days within one month; get hourly, daily, monthly averages
from pytest import approx

@pytest.mark.django_db
def test_random_within_one_month_hour_day_month_averages():
    c = APIClient()
    uid = "u-month-rand"
    # Pick March 2025 (31 days); submit on 5 fixed days, 60 minutes each, 50 words each
    days = [1, 5, 12, 20, 28]
    for i, d in enumerate(days):
        start = timezone.datetime(2025, 3, d, 9, 0, 0, tzinfo=dt.timezone.utc)
        _post_record(c, uid, f"mr-{i}", 50, start, minutes=60)

    total_wc = 50 * 5             # 250
    total_min = 60 * 5            # 300
    f = "2025-03-01T00:00:00Z"
    t = "2025-04-01T00:00:00Z"

    # Hourly averages (active hours = 5)
    r_hour = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=hour&tz=UTC&include_empty=false")
    assert r_hour.status_code == 200
    dh = r_hour.json()
    assert dh["totals"]["word_count"] == total_wc
    assert dh["totals"]["study_minutes"] == total_min
    assert dh["averages_per_bucket"]["word_count"] == total_wc / 5.0     # 50
    assert dh["averages_per_bucket"]["study_minutes"] == total_min / 5.0 # 60

    # Daily averages: compare include_empty=false vs true
    r_day_false = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=day&tz=UTC&include_empty=false")
    r_day_true  = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=day&tz=UTC&include_empty=true")
    assert r_day_false.status_code == 200 and r_day_true.status_code == 200
    ddf = r_day_false.json()
    ddt = r_day_true.json()
    # false -> denominator = 5 active days
    assert ddf["averages_per_bucket"]["word_count"] == total_wc / 5.0     # 50
    assert ddf["averages_per_bucket"]["study_minutes"] == total_min / 5.0 # 60
    # true  -> denominator = 31 days
    assert ddt["averages_per_bucket"]["word_count"] == approx(total_wc / 31.0, rel=1e-6)
    assert ddt["averages_per_bucket"]["study_minutes"] == approx(total_min / 31.0, rel=1e-6)

    # Monthly averages: single-month window -> denominator = 1
    r_month = c.get(f"/api/users/{uid}/summary?from={f}&to={t}&granularity=month&tz=UTC&include_empty=true")
    assert r_month.status_code == 200
    dm = r_month.json()
    assert dm["averages_per_bucket"]["word_count"] == total_wc
    assert dm["averages_per_bucket"]["study_minutes"] == total_min
