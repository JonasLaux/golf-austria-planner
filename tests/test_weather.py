"""Synthetic fixtures only; no real bookings or weather calls."""

import copy
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from golf_austria.weather import EXPECTED_UNITS, fetch_forecast, rank_candidates

NOW = datetime(2026, 11, 16, 8, tzinfo=UTC)


def forecast(day="2026-11-16"):
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return {
        "timezone": "GMT",
        "utc_offset_seconds": 0,
        "fetched_at": NOW.isoformat(),
        "hourly_units": dict(EXPECTED_UNITS),
        "hourly": {
            "time": [(start + timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M") for n in range(25)],
            "temperature_2m": [15] * 25,
            "apparent_temperature": [15] * 25,
            "precipitation_probability": [10] * 25,
            "precipitation": [0] * 25,
            "weather_code": [0] * 25,
            "wind_speed_10m": [5] * 25,
            "wind_gusts_10m": [10] * 25,
        },
        "daily": {
            "time": [day],
            "sunrise": [day + "T06:00"],
            "sunset": [day + "T16:00"],
        },
    }


def candidate(**changes):
    value = {
        "id": "fixture",
        "club": "Fixture Club",
        "course": "Fixture Course",
        "start": "2026-11-16T10:30:00+01:00",
        "holes": 18,
        "duration_minutes": 240,
        "available_players": 2,
        "price_eur": 50,
        "latitude": 48.2,
        "longitude": 16.3,
        "source_url": "https://example.invalid/slots",
        "verified_at": "2026-11-16T08:00:00+00:00",
    }
    value.update(changes)
    return value


class WeatherRankingTests(unittest.TestCase):
    def rank(self, slot=None, data=None, preferences=None):
        return rank_candidates(
            [slot or candidate()], {"Fixture Club": data or forecast()}, preferences, now=NOW
        )[0]

    def test_storm_late_in_round_rejects_otherwise_clear_start(self):
        data = forecast()
        data["hourly"]["weather_code"][13] = 95
        ranked = self.rank(data=data)
        self.assertFalse(ranked["eligible"])
        self.assertIn("Thunderstorms forecast during the round.", ranked["reasons"])

    def test_precipitation_and_gusts_are_preceding_hour_values(self):
        data = forecast()
        # Round is 09:30–13:30 UTC: samples 10..14 describe overlapping hours.
        data["hourly"]["precipitation"][9] = 20  # Ended before the round.
        data["hourly"]["precipitation"][10] = 2
        data["hourly"]["precipitation"][14] = 3
        data["hourly"]["precipitation"][15] = 40  # Starts after the round.
        data["hourly"]["precipitation_probability"][14] = 80
        data["hourly"]["wind_gusts_10m"][14] = 45
        ranked = self.rank(data=data, preferences={"max_gust_kmh": 30})
        self.assertEqual(ranked["weather"]["precipitation_mm_overlapping_hours"], 5)
        self.assertEqual(ranked["weather"]["rain_probability_max_pct"], 80)
        self.assertEqual(ranked["weather"]["gust_max_kmh"], 45)
        self.assertFalse(ranked["eligible"])

    def test_hour_aligned_round_does_not_include_next_rain_interval(self):
        data = forecast()
        data["hourly"]["precipitation"][9] = 20
        data["hourly"]["precipitation"][14] = 20
        ranked = self.rank(slot=candidate(start="2026-11-16T10:00:00+01:00"), data=data)
        self.assertEqual(ranked["weather"]["precipitation_mm_overlapping_hours"], 0)
        self.assertEqual(ranked["weather"]["hourly_intervals"], 4)

    def test_missing_middle_hour_is_unknown_not_clear(self):
        data = forecast()
        data["hourly"]["precipitation_probability"][12] = None
        ranked = self.rank(data=data)
        self.assertIsNone(ranked["score"])
        self.assertFalse(ranked["eligible"])
        self.assertEqual(ranked["status"], "incomplete")
        self.assertTrue(
            any("precipitation_probability" in value for value in ranked["uncertainties"])
        )

    def test_missing_forecast_is_unknown(self):
        ranked = rank_candidates([candidate()], {}, now=NOW)[0]
        self.assertFalse(ranked["bookable"])
        self.assertIsNone(ranked["score"])
        self.assertIn("No forecast available for this club.", ranked["uncertainties"])

    def test_unknown_price_cannot_pass_budget_check(self):
        ranked = self.rank(slot=candidate(price_eur=None), preferences={"max_price_eur": 60})
        self.assertFalse(ranked["bookable"])
        self.assertIsNotNone(ranked["score"])
        self.assertEqual(ranked["status"], "incomplete")
        self.assertTrue(any("price is unknown" in value for value in ranked["uncertainties"]))

    def test_spaces_and_per_player_budget_are_hard_limits(self):
        ranked = self.rank(
            slot=candidate(available_players=1, price_eur=65),
            preferences={"player_count": 2, "max_price_eur": 60},
        )
        self.assertFalse(ranked["eligible"])
        self.assertEqual(len(ranked["reasons"]), 2)

    def test_sunset_buffer_includes_whole_round(self):
        ranked = self.rank(slot=candidate(start="2026-11-16T13:00:00+01:00"))
        self.assertIn("Round would finish too close to or after sunset.", ranked["reasons"])
        ranked = self.rank(
            slot=candidate(start="2026-11-16T13:00:00+01:00"),
            preferences={"sunset_buffer_minutes": 0},
        )
        self.assertTrue(ranked["eligible"])

    def test_dst_offsets_are_compared_as_instants(self):
        now = datetime(2026, 10, 24, 23, 55, tzinfo=UTC)
        data = forecast("2026-10-25")
        data["daily"]["sunrise"] = ["2026-10-25T00:00"]
        data["fetched_at"] = now.isoformat()
        early = candidate(
            id="first",
            start="2026-10-25T02:30:00+02:00",
            duration_minutes=120,
            verified_at=now.isoformat(),
        )
        late = candidate(
            id="second",
            start="2026-10-25T02:30:00+01:00",
            duration_minutes=120,
            verified_at=now.isoformat(),
        )
        ranked = rank_candidates([late, early], {"Fixture Club": data}, now=now)
        self.assertEqual([row["id"] for row in ranked], ["first", "second"])
        self.assertEqual(ranked[0]["end"], "2026-10-25T02:30:00+00:00")
        self.assertEqual(ranked[1]["end"], "2026-10-25T03:30:00+00:00")

    def test_naive_candidate_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            self.rank(slot=candidate(start="2026-11-16T10:00:00"))

    def test_units_must_not_be_silently_assumed(self):
        data = forecast()
        data["hourly_units"]["wind_speed_10m"] = "mph"
        ranked = self.rank(data=data)
        self.assertFalse(ranked["eligible"])
        self.assertIsNone(ranked["score"])

    def test_synthetic_candidate_is_never_bookable(self):
        ranked = self.rank(slot=candidate(synthetic=True))
        self.assertFalse(ranked["bookable"])
        self.assertIn("Synthetic example; this is not a real tee time.", ranked["reasons"])

    def test_ranker_does_not_mutate_inputs(self):
        candidates = [candidate()]
        forecasts = {"Fixture Club": forecast()}
        original = copy.deepcopy((candidates, forecasts))
        rank_candidates(candidates, forecasts, now=NOW)
        self.assertEqual((candidates, forecasts), original)

    def test_past_slot_is_excluded_even_with_fresh_data(self):
        ranked = self.rank(slot=candidate(start=NOW.isoformat()))
        self.assertFalse(ranked["eligible"])
        self.assertIn("Tee time has already started.", ranked["reasons"])

    def test_stale_availability_is_unknown_and_limit_can_be_configured(self):
        slot = candidate(verified_at=(NOW - timedelta(minutes=20)).isoformat())
        ranked = self.rank(slot=slot)
        self.assertFalse(ranked["eligible"])
        self.assertTrue(any("Availability is stale" in item for item in ranked["uncertainties"]))
        ranked = self.rank(slot=slot, preferences={"max_availability_age_minutes": 30})
        self.assertTrue(ranked["eligible"])
        self.assertFalse(ranked["bookable"])

    def test_stale_forecast_blocks_scoring_and_eligibility(self):
        data = forecast()
        data["fetched_at"] = (NOW - timedelta(hours=7)).isoformat()
        ranked = self.rank(data=data)
        self.assertFalse(ranked["eligible"])
        self.assertIsNone(ranked["score"])
        self.assertTrue(any("Forecast is stale" in item for item in ranked["uncertainties"]))
        ranked = self.rank(data=data, preferences={"max_forecast_age_hours": 8})
        self.assertTrue(ranked["eligible"])

    def test_missing_forecast_timestamp_blocks_scoring(self):
        data = forecast()
        data.pop("fetched_at")
        ranked = self.rank(data=data)
        self.assertFalse(ranked["eligible"])
        self.assertIsNone(ranked["score"])
        self.assertIn("Forecast has no valid verification timestamp.", ranked["uncertainties"])

    def test_future_timestamps_outside_clock_skew_are_unknown(self):
        future = (NOW + timedelta(minutes=6)).isoformat()
        data = forecast()
        data["fetched_at"] = future
        ranked = self.rank(slot=candidate(verified_at=future), data=data)
        self.assertFalse(ranked["eligible"])
        self.assertEqual(len(ranked["uncertainties"]), 2)
        self.assertTrue(all("future" in item for item in ranked["uncertainties"]))

    def test_default_clock_is_current_utc_time(self):
        calls = []

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                calls.append(tz)
                return cls.fromisoformat(NOW.isoformat())

        with patch("golf_austria.weather.datetime", FrozenDateTime):
            ranked = rank_candidates([candidate()], {"Fixture Club": forecast()})[0]
        self.assertEqual(calls, [UTC])
        self.assertTrue(ranked["eligible"])

    def test_eligible_advice_is_never_a_booking_authorization(self):
        ranked = self.rank()
        self.assertTrue(ranked["eligible"])
        self.assertFalse(ranked["bookable"])
        self.assertTrue(ranked["requires_availability_recheck"])

    def test_plain_course_advisories_do_not_claim_course_is_closed(self):
        notices = [
            "Grass tees on the driving range are closed.",
            "Course closed if there is frost; affected tee times are void.",
            "Fairway aeration Tuesday and Wednesday.",
        ]
        ranked = self.rank(slot=candidate(course_notices=notices))
        self.assertTrue(ranked["eligible"])
        self.assertEqual(ranked["warnings"], notices)
        self.assertEqual(ranked["reasons"], [])

    def test_unresolved_booking_rules_block_eligibility(self):
        ranked = self.rank(slot=candidate(booking_rules_unknown=True))
        self.assertFalse(ranked["eligible"])
        self.assertEqual(ranked["status"], "incomplete")
        self.assertTrue(any("booking rules" in value for value in ranked["uncertainties"]))

    def test_confirmed_course_closure_is_excluded(self):
        ranked = self.rank(slot=candidate(course_closed=True))
        self.assertFalse(ranked["eligible"])
        self.assertIn("Course is marked closed.", ranked["reasons"])

    def test_malformed_closure_flag_does_not_become_confirmed_closure(self):
        ranked = self.rank(slot=candidate(course_closed="false"))
        self.assertFalse(ranked["eligible"])
        self.assertNotIn("Course is marked closed.", ranked["reasons"])
        self.assertIn("Course closure status could not be determined.", ranked["uncertainties"])

    def test_fetch_explicit_units_timeout_and_attribution(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, limit):
                return json.dumps(forecast()).encode()

        with patch("golf_austria.weather.urlopen", return_value=Response()) as fetch:
            result = fetch_forecast(48.2, 16.3)
        request = fetch.call_args.args[0]
        self.assertIn("timezone=UTC", request.full_url)
        self.assertIn("wind_speed_unit=kmh", request.full_url)
        self.assertEqual(fetch.call_args.kwargs["timeout"], 25)
        self.assertIn("fetched_at", result)
        self.assertIn("Open-Meteo", result["attribution"])


if __name__ == "__main__":
    unittest.main()
