"""Fetch Open-Meteo forecasts and rank candidate rounds without making bookings.

Forecasts are keyed by candidate ``club``. Forecast timestamps are requested in
UTC, avoiding ambiguous local times at daylight-saving transitions. Open-Meteo's
rain, rain probability and gust values describe the *preceding* hour; the other
selected variables are instantaneous samples. Both are handled separately.

The score is a transparent preference heuristic, not a chance of a good round:
start at 100, subtract up to 35 for hourly rain probability, 20 for overlapping
hour rainfall, 15 each for wind, gusts and uncomfortable feels-like temperature.
Hard limits and unknown essential values take precedence over that score.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOUR = timedelta(hours=1)
HOURLY_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
)
INSTANT_FIELDS = ("temperature_2m", "apparent_temperature", "weather_code", "wind_speed_10m")
PRECEDING_FIELDS = ("precipitation_probability", "precipitation", "wind_gusts_10m")
EXPECTED_UNITS = {
    "temperature_2m": "°C",
    "apparent_temperature": "°C",
    "precipitation_probability": "%",
    "precipitation": "mm",
    "weather_code": "wmo code",
    "wind_speed_10m": "km/h",
    "wind_gusts_10m": "km/h",
}


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _instant(value: str, *, allow_utc_naive: bool = False) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO 8601 string.")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Timestamp must be an ISO 8601 datetime.") from exc
    if timestamp.tzinfo is None:
        if not allow_utc_naive:
            raise ValueError("Candidate timestamps must include a UTC offset.")
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def fetch_forecast(latitude: float, longitude: float) -> dict:
    """Fetch 16 days of weather in explicit metric units; raise on failure.

    The free Open-Meteo endpoint is suitable for this personal prototype.
    Attribution: https://open-meteo.com/ (CC BY 4.0). See its usage terms before
    deploying commercially. Returned ``fetched_at`` is retrieval time, not the
    weather model's forecast issue time.
    """
    if not _number(latitude) or not -90 <= latitude <= 90:
        raise ValueError("latitude must be a finite number from -90 to 90.")
    if not _number(longitude) or not -180 <= longitude <= 180:
        raise ValueError("longitude must be a finite number from -180 to 180.")
    query = urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "daily": "sunrise,sunset",
            "timezone": "UTC",
            "forecast_days": 16,
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }
    )
    request = Request(
        "https://api.open-meteo.com/v1/forecast?" + query,
        headers={"User-Agent": "golf-austria-personal/0.1", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=25) as response:
            body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise ValueError("Weather response exceeded the expected size.")
        forecast = json.loads(body)
    except HTTPError as exc:
        raise RuntimeError(f"Open-Meteo returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Open-Meteo could not be reached.") from exc
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Open-Meteo returned an invalid JSON response.") from exc
    if not isinstance(forecast, dict) or forecast.get("error"):
        raise RuntimeError("Open-Meteo did not return a forecast.")
    if not isinstance(forecast.get("hourly"), dict) or not isinstance(forecast.get("daily"), dict):
        raise RuntimeError("Open-Meteo response lacks hourly or daylight data.")
    forecast["fetched_at"] = datetime.now(UTC).isoformat()
    forecast["source"] = "https://open-meteo.com/"
    forecast["attribution"] = "Weather data by Open-Meteo, CC BY 4.0"
    return forecast


def _weather_for_round(forecast: dict, start: datetime, end: datetime) -> tuple[dict, list[str]]:
    problems: list[str] = []
    if (
        forecast.get("timezone") not in ("UTC", "GMT", "Etc/UTC", "Etc/GMT")
        or forecast.get("utc_offset_seconds") != 0
    ):
        return {}, ["Forecast must use UTC timestamps (timezone=UTC)."]
    hourly = forecast.get("hourly", {})
    units = forecast.get("hourly_units", {})
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        return {}, ["Hourly forecast data is malformed."]
    try:
        timestamps = [_instant(t, allow_utc_naive=True) for t in hourly.get("time", [])]
    except (ValueError, TypeError):
        return {}, ["Hourly forecast timestamps are malformed."]
    if len(set(timestamps)) != len(timestamps):
        return {}, ["Hourly forecast contains duplicate timestamps."]
    positions = {t: i for i, t in enumerate(timestamps)}
    first_hour = start.replace(minute=0, second=0, microsecond=0)
    last_hour = end.replace(minute=0, second=0, microsecond=0)
    if end > last_hour:
        last_hour += HOUR
    instant_times = []
    cursor = first_hour
    while cursor <= last_hour:
        instant_times.append(cursor)
        cursor += HOUR
    # Values labelled 11:00 describe rain/gusts over 10:00–11:00.
    preceding_times = instant_times[1:]
    values: dict[str, list[float]] = {}
    for field in HOURLY_FIELDS:
        needed = instant_times if field in INSTANT_FIELDS else preceding_times
        series = hourly.get(field, [])
        if units.get(field) != EXPECTED_UNITS[field]:
            problems.append(f"Unrecognised or missing unit for {field}.")
            continue
        if not isinstance(series, list):
            problems.append(f"Malformed forecast for {field}.")
            continue
        selected: list[float] = []
        for timestamp in needed:
            index = positions.get(timestamp)
            value = series[index] if index is not None and index < len(series) else None
            valid = _number(value)
            if valid and field == "precipitation_probability":
                valid = 0 <= value <= 100
            elif valid and field in ("precipitation", "wind_speed_10m", "wind_gusts_10m"):
                valid = value >= 0
            elif valid and field == "weather_code":
                valid = value in (
                    0,
                    1,
                    2,
                    3,
                    45,
                    48,
                    51,
                    53,
                    55,
                    56,
                    57,
                    61,
                    63,
                    65,
                    66,
                    67,
                    71,
                    73,
                    75,
                    77,
                    80,
                    81,
                    82,
                    85,
                    86,
                    95,
                    96,
                    99,
                )
            if not valid:
                problems.append(f"Incomplete or invalid {field} during the round.")
                break
            selected.append(value)
        if len(selected) == len(needed):
            values[field] = selected
    weather: dict = {
        "source": forecast.get("source", "https://open-meteo.com/"),
        "fetched_at": forecast.get("fetched_at"),
        "round_start_utc": start.isoformat(),
        "round_end_utc": end.isoformat(),
        "hourly_window_start_utc": first_hour.isoformat(),
        "hourly_window_end_utc": last_hour.isoformat(),
        "hourly_intervals": len(preceding_times),
        "sampling_note": "Instantaneous weather samples bracket the round; subhour changes are unknown. This is a conservative hourly assessment.",
        "rain_probability_note": "Maximum of overlapping hourly probabilities; not the probability of rain anywhere in the round.",
        "precipitation_note": "Sum over whole overlapping hours; partial first/last hours are included without prorating.",
    }
    aggregations = {
        "temperature_min_c": ("temperature_2m", min),
        "temperature_max_c": ("temperature_2m", max),
        "feels_like_min_c": ("apparent_temperature", min),
        "feels_like_max_c": ("apparent_temperature", max),
        "rain_probability_max_pct": ("precipitation_probability", max),
        "precipitation_mm_overlapping_hours": ("precipitation", sum),
        "wind_max_kmh": ("wind_speed_10m", max),
        "gust_max_kmh": ("wind_gusts_10m", max),
    }
    for output, (field, operation) in aggregations.items():
        weather[output] = round(operation(values[field]), 2) if values.get(field) else None
    weather["weather_codes"] = (
        sorted(set(values["weather_code"])) if values.get("weather_code") else None
    )
    daily = forecast.get("daily", {})
    sunrise = sunset = None
    try:
        daily_times = daily.get("time", [])
        day_index = daily_times.index(start.date().isoformat())
        sunrise = _instant(daily["sunrise"][day_index], allow_utc_naive=True)
        sunset = _instant(daily["sunset"][day_index], allow_utc_naive=True)
        if sunrise >= sunset:
            raise ValueError("Invalid daylight interval")
    except (AttributeError, ValueError, KeyError, IndexError, TypeError):
        problems.append("Sunrise/sunset data is missing or invalid for the round.")
    weather["sunrise_utc"] = sunrise.isoformat() if sunrise else None
    weather["sunset_utc"] = sunset.isoformat() if sunset else None
    return weather, problems


def _preferences(preferences: dict | None) -> dict:
    result = {
        "player_count": 1,
        "sunset_buffer_minutes": 30,
        "max_availability_age_minutes": 15,
        "max_forecast_age_hours": 6,
    }
    result.update(preferences or {})
    for key in ("player_count", "sunset_buffer_minutes"):
        value = result[key]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < (1 if key == "player_count" else 0)
        ):
            raise ValueError(
                f"{key} must be {'positive' if key == 'player_count' else 'nonnegative'} integer."
            )
    for key in (
        "max_price_eur",
        "min_temperature_c",
        "max_wind_kmh",
        "max_gust_kmh",
        "max_rain_probability",
    ):
        value = result.get(key)
        if value is not None and (not _number(value) or (key != "min_temperature_c" and value < 0)):
            raise ValueError(
                f"{key} must be a finite {'number' if key == 'min_temperature_c' else 'nonnegative number'}."
            )
    if (
        result.get("max_rain_probability", 0) is not None
        and result.get("max_rain_probability", 0) > 100
    ):
        raise ValueError("max_rain_probability must be between 0 and 100.")
    for key in ("max_availability_age_minutes", "max_forecast_age_hours"):
        if not _number(result[key]) or result[key] <= 0:
            raise ValueError(f"{key} must be a finite positive number.")
    return result


def _freshness_problem(value: str, label: str, now: datetime, max_age: timedelta) -> str | None:
    try:
        timestamp = _instant(value)
    except ValueError:
        return f"{label} has no valid verification timestamp."
    if timestamp > now + timedelta(minutes=5):
        return f"{label} timestamp is more than five minutes in the future."
    if now - timestamp > max_age:
        return f"{label} is stale; refresh it before relying on this recommendation."
    return None


def rank_candidates(
    candidates: list[dict],
    forecasts: dict[str, dict],
    preferences: dict | None = None,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Rank live or imported candidates using club-keyed raw Open-Meteo data.

    ``eligible`` means the candidate meets known constraints with recent data;
    it is advisory, never booking authorization. ``bookable`` is always false:
    this ranker does not implement booking, and live availability and price
    must still be refreshed at submission time. Verification timestamps permit
    at most five minutes of clock skew into the future. Availability defaults
    to a 15-minute maximum age, forecast retrieval time to six hours.
    ``max_price_eur`` is a per-player limit, matching ``price_eur`` inputs.
    Synthetic candidates are always ineligible. Missing forecast data is never
    treated as dry weather, and incomplete weather produces ``score=None``.
    ``course_notices`` become nonblocking warnings. Only an explicit
    ``course_closed=True`` rejects a course; conditional notices do not prove
    closure. ``booking_rules_unknown=True`` marks the recommendation incomplete.
    """
    pref = _preferences(preferences)
    if now is None:
        now = datetime.now(UTC)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime.")
    now = now.astimezone(UTC)
    results = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Each candidate must be a JSON object.")
        reasons: list[str] = []
        uncertainties: list[str] = []
        warnings: list[str] = []
        result = dict(candidate)
        if not candidate.get("club"):
            raise ValueError("Each candidate needs a club name.")
        start = _instant(candidate.get("start"))
        if start <= now:
            reasons.append("Tee time has already started.")
        duration = candidate.get("duration_minutes")
        if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 720:
            raise ValueError("duration_minutes must be an integer from 1 to 720.")
        if candidate.get("holes") not in (9, 18):
            raise ValueError("holes must be 9 or 18.")
        end = start + timedelta(minutes=duration)
        spaces = candidate.get("available_players")
        if not isinstance(spaces, int) or isinstance(spaces, bool) or spaces < 0:
            uncertainties.append("Available player spots are unknown.")
        elif spaces < pref["player_count"]:
            reasons.append("Not enough available player spots.")
        price = candidate.get("price_eur")
        if not _number(price) or price < 0:
            uncertainties.append(
                "Per-player price is unknown; confirm member pricing before booking."
            )
        elif pref.get("max_price_eur") is not None and price > pref["max_price_eur"]:
            reasons.append("Per-player price exceeds the budget.")
        availability_problem = _freshness_problem(
            candidate.get("verified_at"),
            "Availability",
            now,
            timedelta(minutes=pref["max_availability_age_minutes"]),
        )
        if availability_problem:
            uncertainties.append(availability_problem)
        if candidate.get("synthetic"):
            reasons.append("Synthetic example; this is not a real tee time.")
        if candidate.get("course_closed") is True:
            reasons.append("Course is marked closed.")
        elif "course_closed" in candidate and candidate["course_closed"] is not False:
            uncertainties.append("Course closure status could not be determined.")
        notices = candidate.get("course_notices", [])
        if not isinstance(notices, list) or any(not isinstance(notice, str) for notice in notices):
            uncertainties.append("Course notices could not be read reliably.")
        else:
            warnings.extend(dict.fromkeys(notice.strip() for notice in notices if notice.strip()))
        if candidate.get("booking_rules_unknown", False) is not False:
            uncertainties.append(
                "Applicable booking rules are unresolved; verify them with the club."
            )
        forecast = forecasts.get(candidate["club"])
        if not isinstance(forecast, dict):
            weather, weather_problems = {}, ["No forecast available for this club."]
        else:
            weather, weather_problems = _weather_for_round(forecast, start, end)
            forecast_problem = _freshness_problem(
                forecast.get("fetched_at"),
                "Forecast",
                now,
                timedelta(hours=pref["max_forecast_age_hours"]),
            )
            if forecast_problem:
                weather_problems.append(forecast_problem)
        uncertainties.extend(weather_problems)
        codes = weather.get("weather_codes") or []
        if any(code in (95, 96, 99) for code in codes):
            reasons.append("Thunderstorms forecast during the round.")
        if any(code in (56, 57, 66, 67, 71, 73, 75, 77, 85, 86) for code in codes):
            reasons.append("Snow or freezing precipitation forecast during the round.")
        if weather.get("sunrise_utc") and start < _instant(weather["sunrise_utc"]):
            reasons.append("Round starts before sunrise.")
        if weather.get("sunset_utc") and end + timedelta(
            minutes=pref["sunset_buffer_minutes"]
        ) > _instant(weather["sunset_utc"]):
            reasons.append("Round would finish too close to or after sunset.")
        thresholds = (
            (
                "min_temperature_c",
                "temperature_min_c",
                lambda value, limit: value < limit,
                "Temperature falls below your minimum.",
            ),
            (
                "max_wind_kmh",
                "wind_max_kmh",
                lambda value, limit: value > limit,
                "Wind exceeds your limit.",
            ),
            (
                "max_gust_kmh",
                "gust_max_kmh",
                lambda value, limit: value > limit,
                "Wind gusts exceed your limit.",
            ),
            (
                "max_rain_probability",
                "rain_probability_max_pct",
                lambda value, limit: value > limit,
                "An overlapping hour exceeds your rain-probability limit.",
            ),
        )
        for preference, metric, exceeds, message in thresholds:
            if (
                pref.get(preference) is not None
                and weather.get(metric) is not None
                and exceeds(weather[metric], pref[preference])
            ):
                reasons.append(message)
        score = None
        if not weather_problems:
            discomfort = max(0, 12 - weather["feels_like_min_c"], weather["feels_like_max_c"] - 26)
            score = round(
                max(
                    0,
                    100
                    - min(35, 0.35 * weather["rain_probability_max_pct"])
                    - min(20, 5 * weather["precipitation_mm_overlapping_hours"])
                    - min(15, 0.5 * weather["wind_max_kmh"])
                    - min(15, 0.5 * max(0, weather["gust_max_kmh"] - 20))
                    - min(15, 2 * discomfort),
                )
            )
        eligible = not reasons and not uncertainties
        result.update(
            {
                "end": end.isoformat(),
                "score": score,
                "eligible": eligible,
                "bookable": False,
                "status": "excluded"
                if reasons
                else ("incomplete" if uncertainties else "recommended"),
                "reasons": reasons,
                "uncertainties": uncertainties,
                "warnings": warnings,
                "weather": weather,
                "requires_availability_recheck": True,
            }
        )
        results.append(result)
    return sorted(
        results,
        key=lambda row: (
            not row["eligible"],
            bool(row["reasons"]),
            row["score"] is None,
            -(row["score"] or 0),
            _instant(row["start"]),
            str(row.get("id", "")),
        ),
    )
