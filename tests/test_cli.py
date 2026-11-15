"""Recommendation orchestration with synthetic provider boundaries."""

import io
import json
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError

from golf_austria import cli
from golf_austria.browser import GolfError

CLUBS = {"1": {"name": "Unavailable fixture club"}, "2": {"name": "Working fixture club"}}


def available_candidate(holes, duration):
    return {
        "id": "fixture-slot",
        "club": "Working fixture club",
        "start": "2026-11-16T10:30:00+01:00",
        "holes": holes,
        "duration_minutes": duration,
        "latitude": 48.1,
        "longitude": 16.4,
    }


def rank_fixture(candidates, forecasts, preferences):
    # Weather/ranking internals have separate tests. Exercise aggregation here.
    return [{**candidate, "eligible": True} for candidate in candidates]


class RecommendationCliTests(unittest.TestCase):
    def test_failed_club_preserves_other_results_and_returns_partial_exit(self):
        for failure in (
            GolfError("Club unavailable"),
            PlaywrightError("browser error with SYNTHETIC_SECRET"),
        ):
            with self.subTest(failure=type(failure).__name__):

                def availability(club_id, requested_date, state_dir, **kwargs):
                    if club_id == "1":
                        raise failure
                    return {
                        "status": "ok",
                        "candidates": [available_candidate(9, kwargs["duration_minutes"])],
                        "notices": [],
                    }

                with ExitStack() as stack:
                    stack.enter_context(patch.object(cli, "configured_clubs", return_value=CLUBS))
                    lookup = stack.enter_context(
                        patch.object(cli, "availability", side_effect=availability)
                    )
                    weather = stack.enter_context(
                        patch.object(cli, "fetch_forecast", return_value={"fixture": True})
                    )
                    stack.enter_context(
                        patch.object(cli, "rank_candidates", side_effect=rank_fixture)
                    )
                    stack.enter_context(
                        patch("sys.argv", ["golf", "recommend", "--date", "2026-11-16"])
                    )
                    output = io.StringIO()
                    with redirect_stdout(output):
                        exit_code = cli.main()
                result = json.loads(output.getvalue())
                self.assertEqual(exit_code, 2)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(
                    [row["status"] for row in result["clubs"]], ["request_failed", "ok"]
                )
                self.assertIsNone(result["clubs"][0]["candidate_count"])
                self.assertEqual(result["candidates"][0]["id"], "fixture-slot")
                self.assertEqual(result["eligible_candidates"], 1)
                self.assertEqual(lookup.call_count, 2)
                weather.assert_called_once_with(48.1, 16.4)
                self.assertNotIn("SYNTHETIC_SECRET", output.getvalue())

    def test_round_duration_defaults_follow_holes_and_allow_explicit_override(self):
        for holes, override, expected in ((9, None, 120), (18, None, 270), (18, 300, 300)):
            with self.subTest(holes=holes, override=override):
                arguments = [
                    "recommend",
                    "--date",
                    "2026-11-16",
                    "--club",
                    "2",
                    "--holes",
                    str(holes),
                ]
                if override is not None:
                    arguments.extend(["--duration", str(override)])
                args = cli.parser().parse_args(arguments)

                def availability(club_id, requested_date, state_dir, **kwargs):
                    return {
                        "status": "ok",
                        "candidates": [available_candidate(holes, kwargs["duration_minutes"])],
                        "notices": [],
                    }

                with ExitStack() as stack:
                    stack.enter_context(patch.object(cli, "configured_clubs", return_value=CLUBS))
                    lookup = stack.enter_context(
                        patch.object(cli, "availability", side_effect=availability)
                    )
                    stack.enter_context(
                        patch.object(cli, "fetch_forecast", return_value={"fixture": True})
                    )
                    stack.enter_context(
                        patch.object(cli, "rank_candidates", side_effect=rank_fixture)
                    )
                    result = cli.run(args)
                self.assertEqual(lookup.call_args.kwargs["duration_minutes"], expected)
                self.assertEqual(result["assumptions"]["duration_minutes"], expected)
                self.assertEqual(result["candidates"][0]["duration_minutes"], expected)


if __name__ == "__main__":
    unittest.main()
