"""Command line interface for local golf planning."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .browser import DEFAULT_STATE, GolfError, login, session_status
from .clubs import search_clubs
from .portal import (
    DEFAULT_CLUBS,
    availability,
    configured_clubs,
    inspect_calendar,
    open_booking_browser,
)
from .weather import fetch_forecast, rank_candidates


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def parser():
    root = argparse.ArgumentParser(
        description="Local Golf Austria login, club lookup and weather ranking."
    )
    root.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE, help="Private browser-state directory"
    )
    commands = root.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("login", help="Sign in yourself in a dedicated visible browser")
    auth.add_argument("--timeout", type=int, default=900)
    commands.add_parser("status", help="Check the saved login session")
    clubs = commands.add_parser("clubs", help="Search Golf Austria's public club directory")
    clubs.add_argument("query")
    inspect = commands.add_parser(
        "inspect", help="Check authenticated calendar access and structure"
    )
    inspect.add_argument("--club", required=True, help="Numeric Golf Austria club ID")
    times = commands.add_parser("times", help="Read live tee times without touching checkout")
    times.add_argument("--club", required=True)
    times.add_argument("--date", required=True, help="YYYY-MM-DD, Europe/Vienna")
    times.add_argument(
        "--duration",
        type=int,
        help="Expected round duration in minutes (default 120 for 9 holes, 270 for 18)",
    )
    times.add_argument("--clubs-file", type=Path, default=DEFAULT_CLUBS)
    plan = commands.add_parser(
        "recommend", help="Combine live availability and whole-round weather for your clubs"
    )
    plan.add_argument("--date", required=True, help="YYYY-MM-DD, Europe/Vienna")
    plan.add_argument(
        "--club", action="append", help="Club ID; repeat to compare clubs (default all configured)"
    )
    plan.add_argument("--clubs-file", type=Path, default=DEFAULT_CLUBS)
    plan.add_argument(
        "--duration",
        type=int,
        help="Expected round duration in minutes (default 120 for 9 holes, 270 for 18)",
    )
    plan.add_argument("--holes", type=int, choices=(9, 18), default=9)
    plan.add_argument("--players", type=int, default=1)
    plan.add_argument("--max-price", type=float)
    plan.add_argument("--max-rain", type=float)
    plan.add_argument("--max-wind", type=float)
    plan.add_argument("--max-gust", type=float)
    plan.add_argument(
        "--min-temperature", type=float, help="Minimum air temperature in Celsius during the round"
    )
    plan.add_argument("--after", help="Earliest local tee time, HH:MM")
    plan.add_argument("--before", help="Latest local tee time, HH:MM")
    plan.add_argument("--limit", type=int, default=5)
    handoff = commands.add_parser("open", help="Open the logged-in calendar for booking yourself")
    handoff.add_argument("--club", required=True)
    handoff.add_argument("--date", required=True)
    handoff.add_argument("--clubs-file", type=Path, default=DEFAULT_CLUBS)
    weather = commands.add_parser(
        "weather", help="Fetch hourly Open-Meteo weather at course coordinates"
    )
    weather.add_argument("--latitude", required=True, type=float)
    weather.add_argument("--longitude", required=True, type=float)
    rank = commands.add_parser("rank", help="Rank a JSON list of verified tee-time candidates")
    rank.add_argument("candidates", type=Path)
    rank.add_argument(
        "--forecasts", type=Path, help="Use saved forecasts keyed by club name instead of fetching"
    )
    rank.add_argument("--preferences", type=Path, help="JSON preference object")
    rank.add_argument("--players", type=int)
    rank.add_argument("--max-price", type=float, help="Maximum EUR per player")
    rank.add_argument(
        "--max-rain", type=float, help="Maximum overlapping hourly rain probability, percent"
    )
    rank.add_argument("--max-wind", type=float, help="Maximum wind in km/h")
    rank.add_argument("--max-gust", type=float, help="Maximum gust in km/h")
    rank.add_argument(
        "--min-temperature", type=float, help="Minimum air temperature in Celsius during the round"
    )
    return root


def run(args):
    if args.command == "login":
        if not 1 <= args.timeout <= 3600:
            raise ValueError("Login timeout must be between 1 and 3600 seconds.")
        return login(args.state_dir, timeout_seconds=args.timeout)
    if args.command == "status":
        return session_status(args.state_dir)
    if args.command == "clubs":
        return {"query": args.query, "clubs": search_clubs(args.query)}
    if args.command == "inspect":
        return inspect_calendar(args.club, args.state_dir)
    if args.command == "times":
        if args.duration is not None and not 1 <= args.duration <= 720:
            raise ValueError("Round duration must be between 1 and 720 minutes.")
        return availability(
            args.club,
            args.date,
            args.state_dir,
            duration_minutes=args.duration,
            clubs_file=args.clubs_file,
        )
    if args.command == "open":
        return open_booking_browser(
            args.club, args.date, args.state_dir, clubs_file=args.clubs_file
        )
    if args.command == "recommend":
        return recommend(args)
    if args.command == "weather":
        return fetch_forecast(args.latitude, args.longitude)
    if args.command == "rank":
        candidates = read_json(args.candidates)
        if not isinstance(candidates, list):
            raise ValueError("Candidates JSON must be an array.")
        preferences = read_json(args.preferences) if args.preferences else {}
        if not isinstance(preferences, dict):
            raise ValueError("Preferences JSON must be an object.")
        for argument, preference in (
            ("players", "player_count"),
            ("max_price", "max_price_eur"),
            ("max_rain", "max_rain_probability"),
            ("max_wind", "max_wind_kmh"),
            ("max_gust", "max_gust_kmh"),
            ("min_temperature", "min_temperature_c"),
        ):
            value = getattr(args, argument)
            if value is not None:
                preferences[preference] = value
        forecasts = read_json(args.forecasts) if args.forecasts else {}
        if not isinstance(forecasts, dict):
            raise ValueError("Forecast JSON must be an object keyed by club name.")
        errors = {}
        locations = {}
        if not args.forecasts:
            for candidate in candidates:
                if not isinstance(candidate, dict) or not isinstance(candidate.get("club"), str):
                    raise ValueError("Each candidate must have a club name.")
                club = candidate["club"]
                coordinates = (candidate.get("latitude"), candidate.get("longitude"))
                if club in locations and locations[club] != coordinates:
                    raise ValueError(
                        "Candidates sharing a club name must use the same coordinates."
                    )
                locations[club] = coordinates
            for club, coordinates in locations.items():
                try:
                    forecasts[club] = fetch_forecast(*coordinates)
                except (RuntimeError, ValueError) as exc:
                    errors[club] = str(exc)
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "forecast_errors": errors,
            "candidates": rank_candidates(candidates, forecasts, preferences),
            "attribution": "Weather data by Open-Meteo (https://open-meteo.com/), CC BY 4.0",
        }
    raise ValueError("Unknown command.")


def recommend(args):
    from datetime import date, time
    from zoneinfo import ZoneInfo

    date.fromisoformat(args.date)
    duration = args.duration if args.duration is not None else (120 if args.holes == 9 else 270)
    after = time.fromisoformat(args.after) if args.after else None
    before = time.fromisoformat(args.before) if args.before else None
    if (after and after.tzinfo) or (before and before.tzinfo):
        raise ValueError("--after/--before are local wall times without timezone offsets.")
    if after and before and after > before:
        raise ValueError("--after must not be later than --before.")
    if not 1 <= duration <= 720 or not 1 <= args.limit <= 100:
        raise ValueError("Duration must be 1–720 minutes and limit 1–100.")
    clubs = configured_clubs(args.clubs_file)
    chosen = list(dict.fromkeys(args.club or clubs.keys()))
    if any(club not in clubs for club in chosen):
        raise ValueError("Unknown configured club ID.")
    candidates, outcomes, forecasts, weather_errors, notices = [], [], {}, {}, {}
    for club_id in chosen:
        try:
            result = availability(
                club_id,
                args.date,
                args.state_dir,
                duration_minutes=duration,
                clubs_file=args.clubs_file,
            )
        except (GolfError, RuntimeError, ValueError) as exc:
            result = {"status": "request_failed", "candidates": None, "message": str(exc)}
        except Exception:
            result = {
                "status": "request_failed",
                "candidates": None,
                "message": "The club's browser request failed; no availability could be verified.",
            }
        outcomes.append(
            {
                "club_id": club_id,
                "club": clubs[club_id]["name"],
                "status": result["status"],
                "candidate_count": len(result["candidates"])
                if result.get("candidates") is not None
                else None,
                "message": result.get("message"),
            }
        )
        notices[clubs[club_id]["name"]] = result.get("notices", [])
        for candidate in result.get("candidates") or []:
            local_time = (
                datetime.fromisoformat(candidate["start"])
                .astimezone(ZoneInfo("Europe/Vienna"))
                .time()
            )
            if (
                candidate["holes"] != args.holes
                or (after and local_time < after)
                or (before and local_time > before)
            ):
                continue
            candidates.append(candidate)
    for club_name in dict.fromkeys(candidate["club"] for candidate in candidates):
        location = next(candidate for candidate in candidates if candidate["club"] == club_name)
        try:
            forecasts[club_name] = fetch_forecast(location["latitude"], location["longitude"])
        except (RuntimeError, ValueError) as exc:
            weather_errors[club_name] = str(exc)
    preferences = {
        "player_count": args.players,
        "max_price_eur": args.max_price,
        "max_rain_probability": args.max_rain,
        "max_wind_kmh": args.max_wind,
        "max_gust_kmh": args.max_gust,
        "min_temperature_c": args.min_temperature,
    }
    ranked = rank_candidates(candidates, forecasts, preferences)
    partial = bool(weather_errors) or any(outcome["status"] != "ok" for outcome in outcomes)
    return {
        "status": "partial" if partial else "ok",
        "date": args.date,
        "timezone": "Europe/Vienna",
        "generated_at": datetime.now(UTC).isoformat(),
        "clubs": outcomes,
        "course_notices": notices,
        "assumptions": {
            "holes": args.holes,
            "duration_minutes": duration,
            "player_count": args.players,
            "sunset_buffer_minutes": 30,
            "price_basis": "EUR per player",
        },
        "preferences": preferences,
        "matching_candidates": len(ranked),
        "eligible_candidates": sum(candidate["eligible"] for candidate in ranked),
        "candidates": ranked[: args.limit],
        "forecast_errors": weather_errors,
        "booking": "Read-only: open a club calendar with golf open to complete a booking yourself.",
        "attribution": "Weather data by Open-Meteo (https://open-meteo.com/), CC BY 4.0",
    }


def main():
    args = parser().parse_args()
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        partial_statuses = {
            "authentication_required",
            "access_denied",
            "unsupported_booking_provider",
            "unavailable",
            "login_timeout",
            "browser_closed",
            "partial",
            "calendar_requires_mapping",
        }
        return (
            2
            if isinstance(result, dict)
            and (result.get("status") in partial_statuses or result.get("forecast_errors"))
            else 0
        )
    except KeyboardInterrupt:
        print("Interrupted; any browser session remains private.", file=sys.stderr)
        return 130
    except (GolfError, RuntimeError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception:
        # Browser exceptions can contain HTML or entered field values. Don't dump them.
        print(
            json.dumps(
                {
                    "error": "Browser operation failed. Check the browser installation and network, then retry."
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
