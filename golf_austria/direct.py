"""Read the observed public calendar endpoint without a browser or account state."""

import re
from datetime import date
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .browser import GolfError
from .calendar import parse_calendar
from .portal import DEFAULT_CLUBS, calendar_url, configured_clubs

CALENDAR_URL = "https://teetimes.golf.at/mp1/search"
MAX_HTML_BYTES = 4_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even a checkout GET can create a basket; never follow a redirect.
        return None


def availability(
    club_id,
    requested_date,
    *,
    hour=6,
    minute=0,
    duration_minutes=None,
    clubs_file=DEFAULT_CLUBS,
):
    """Map the provider's read-only search parameters to anonymous calendar JSON."""
    target = date.fromisoformat(requested_date)
    calendar_url(club_id)
    if (
        type(hour) is not int
        or not 0 <= hour <= 23
        or type(minute) is not int
        or not 0 <= minute <= 59
    ):
        raise ValueError("Hour must be 0–23 and minute must be 0–59.")
    if duration_minutes is not None and (
        type(duration_minutes) is not int or not 1 <= duration_minutes <= 720
    ):
        raise ValueError("Duration must be an integer from 1 to 720 minutes.")
    club = configured_clubs(clubs_file).get(str(club_id))
    if club is None:
        raise ValueError("Unknown configured club ID.")
    query = {
        "date": target.strftime("%d.%m.%Y"),
        "hour": hour,
        "minute": minute,
        "searchType": "teeTime",
        "restricted": "true",
    }
    metadata = {
        "club_id": str(club_id),
        "club": club["name"],
        "date": target.isoformat(),
        "transport": "direct_http",
        "authentication": "public",
        "query": query,
    }
    issuer = club.get("calendar_issuer_id")
    courses = club.get("calendar_course_names")
    if not issuer or not courses:
        return {
            **metadata,
            "status": "calendar_requires_mapping",
            "candidates": None,
            "slots": None,
            "message": "Direct reads require a verified calendar issuer and course-name mapping.",
        }
    if not isinstance(issuer, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", issuer):
        raise ValueError("The configured calendar issuer is invalid.")
    if not isinstance(courses, list) or any(
        not isinstance(course, str) or not course.strip() for course in courses
    ):
        raise ValueError("Configured calendar course names must be a list of nonempty names.")
    url = CALENDAR_URL + "?" + urlencode({"issuerId": issuer, **query})
    request = Request(
        url, headers={"User-Agent": "GolfAustriaLocalPlanner/0.1", "Accept": "text/html"}
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            if response.url != url or response.status != 200:
                raise GolfError("The direct calendar request returned an unexpected response.")
            if response.headers.get_content_type() != "text/html":
                raise GolfError("The direct calendar response was not HTML.")
            body = response.read(MAX_HTML_BYTES + 1)
            if len(body) > MAX_HTML_BYTES:
                raise GolfError("The direct calendar response is unexpectedly large.")
            html = body.decode(response.headers.get_content_charset() or "utf-8")
    except HTTPError as exc:
        status = exc.code
        exc.close()
        if status in {401, 403}:
            return {
                **metadata,
                "status": "authentication_required" if status == 401 else "access_denied",
                "candidates": None,
                "slots": None,
                "message": "The provider did not allow this public calendar request.",
            }
        if 300 <= status < 400:
            raise GolfError("The calendar redirected; no redirect was followed.") from None
        raise GolfError(f"The direct calendar request failed (HTTP {status}).") from None
    except (OSError, HTTPException, UnicodeError, LookupError):
        raise GolfError("Could not read the direct calendar endpoint.") from None
    return {
        **metadata,
        **parse_calendar(
            html,
            club=club,
            requested_date=target.isoformat(),
            source_url=url,
            duration_minutes=duration_minutes,
        ),
    }
