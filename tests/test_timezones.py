"""The bots must behave the same whatever timezone the *server* is set to.

Convention: a naive ``datetime`` anywhere in the bots means **naive UTC** (``helpers.utcnow()``,
``helpers.epoch``, the ``wait_until`` argument, ...); aware datetimes are used as they are.  The host's
local time is never consulted.  Each test runs under several host timezones (``TZ`` + ``time.tzset()``),
including half-hour offsets and both hemispheres' daylight saving.
"""

import asyncio
import calendar
import datetime
import time
import types

import discord
import pytest
import pytz

from .. import async_helpers, helpers

pytestmark = pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset (not available on Windows)")

HOST_TIMEZONES = ["UTC", "America/New_York", "Asia/Kolkata", "Pacific/Auckland", "America/St_Johns"]
WINTER = datetime.datetime(2026, 1, 15, 12, 30, 15)  # naive UTC, north-hemisphere standard time
SUMMER = datetime.datetime(2026, 7, 4, 12, 30, 15)  # naive UTC, north-hemisphere daylight saving time
UNIX_2000 = calendar.timegm((2000, 1, 1, 0, 0, 0, 0, 0, 0))


def unix(naive_utc):
    """Unix timestamp of a naive-UTC datetime, computed without touching local time."""
    return calendar.timegm(naive_utc.timetuple())


@pytest.fixture(params=HOST_TIMEZONES)
def host_tz(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def test_the_fixture_really_changes_the_host_timezone(host_tz):
    offset = datetime.datetime.fromtimestamp(unix(WINTER)).astimezone().utcoffset()
    expected = {
        "UTC": 0,
        "America/New_York": -5 * 3600,
        "Asia/Kolkata": 5.5 * 3600,
        "Pacific/Auckland": 13 * 3600,
        "America/St_Johns": -3.5 * 3600,
    }[host_tz]
    assert offset.total_seconds() == expected


@pytest.mark.parametrize("naive", [WINTER, SUMMER])
def test_localize_reads_a_naive_datetime_as_utc(host_tz, naive):
    out = helpers.localize(naive)
    assert out.utcoffset() == datetime.timedelta(0)
    assert out.replace(tzinfo=None) == naive  # same wall clock: it was UTC all along
    assert out == pytz.utc.localize(naive)


def test_localize_leaves_aware_datetimes_alone(host_tz):
    aware = pytz.timezone("Asia/Tokyo").localize(SUMMER)
    assert helpers.localize(aware) is aware


def test_delocalize_gives_naive_utc(host_tz):
    aware = pytz.timezone("US/Pacific").localize(datetime.datetime(2026, 7, 4, 5, 30, 15))
    assert helpers.delocalize(aware) == SUMMER
    assert helpers.delocalize(WINTER) is WINTER


@pytest.mark.parametrize("naive", [WINTER, SUMMER])
def test_int_time_is_exact(host_tz, naive):
    assert helpers.int_time(naive, "unix") == unix(naive)
    assert helpers.int_time(naive, 0) == unix(naive)
    assert helpers.int_time(naive) == unix(naive) - UNIX_2000  # default t0: helpers.epoch


@pytest.mark.parametrize("naive", [WINTER, SUMMER])
def test_int_time_accepts_aware_datetimes(host_tz, naive):
    aware = pytz.timezone("Europe/Berlin").localize(naive + datetime.timedelta(hours=6))
    assert helpers.int_time(aware, "unix") == unix(naive) + 6 * 3600 - aware.utcoffset().total_seconds()


@pytest.mark.parametrize("naive", [WINTER, SUMMER])
def test_int_time_round_trips_through_seconds_to_datetime(host_tz, naive):
    assert helpers.seconds_to_datetime(helpers.int_time(naive)) == pytz.utc.localize(naive)
    assert helpers.seconds_to_datetime(helpers.int_time(naive, "unix"), t0=datetime.datetime(1970, 1, 1)) == (
        pytz.utc.localize(naive)
    )


def test_int_time_now_matches_the_clock(host_tz):
    assert abs(helpers.int_time() - (time.time() - UNIX_2000)) <= 2
    assert abs(helpers.int_time(t0="unix") - time.time()) <= 2


def test_utcnow_is_utc_whatever_the_host_says(host_tz):
    assert abs(unix(helpers.utcnow()) - time.time()) <= 2


@pytest.mark.parametrize("make", [helpers.utcnow, lambda: datetime.datetime.now(datetime.UTC)], ids=["naive", "aware"])
async def test_wait_until_waits_for_the_given_utc_time_and_no_longer(host_tz, make):
    # west of UTC the old code returned at once, east of UTC it slept for hours
    start = time.monotonic()
    await asyncio.wait_for(async_helpers.wait_until(make() + datetime.timedelta(seconds=0.3)), timeout=3)
    assert 0.25 <= time.monotonic() - start < 3


async def test_wait_until_a_past_time_returns_at_once(host_tz):
    await asyncio.wait_for(async_helpers.wait_until(helpers.utcnow() - datetime.timedelta(hours=7)), timeout=3)


async def test_sleep_fallback_waits_for_the_remainder(host_tz, monkeypatch):
    slept = []

    async def failing_sleep(dt):
        raise discord.HTTPException(types.SimpleNamespace(status=500, reason="x"), "boom")

    monkeypatch.setattr(asyncio, "sleep", failing_sleep)
    monkeypatch.setattr(async_helpers.time, "sleep", slept.append)
    await async_helpers.sleep(5)
    assert len(slept) == 1 and 4.9 <= slept[0] <= 5

