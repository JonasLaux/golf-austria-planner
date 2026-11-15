"""Read-only inspection of Golf Austria's authenticated tee-time calendar."""

import json
import re
import time
from datetime import UTC, datetime
from datetime import date as calendar_date
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .browser import DEFAULT_STATE, GolfError, browser_session, safe_url

DEFAULT_CLUBS = Path(__file__).resolve().parent / "data" / "clubs.json"


def configured_clubs(path=DEFAULT_CLUBS):
    clubs = json.loads(Path(path).read_text())["clubs"]
    return {str(club["id"]): club for club in clubs}


def calendar_url(club_id: str) -> str:
    if not re.fullmatch(r"\d{1,6}", str(club_id)):
        raise ValueError("club_id must be Golf Austria's numeric club ID.")
    return f"https://www.golf.at/tt/startzeiten.asp?clubnr={club_id}"


def open_calendar(page, club_id, requested_date=None, hour=6, *, expected_issuer=None):
    """Follow the site's own login handoff, then change read-only search filters."""
    response = page.goto(calendar_url(club_id), wait_until="domcontentloaded", timeout=45000)
    if response is None or response.status >= 400:
        raise GolfError("Golf Austria's calendar could not be loaded.")
    body = page.locator("body").inner_text()
    if "Userdaten nicht vorhanden" in body or page.locator('input[name="loginpassword"]').count():
        return None, "authentication_required"
    if "Kein Zugriff auf diesen Bereich" in body:
        return None, "access_denied"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        frame = next(
            (
                f
                for f in page.frames
                if urlsplit(f.url).hostname == "teetimes.golf.at"
                and urlsplit(f.url).path == "/mp1/search"
            ),
            None,
        )
        if frame is not None:
            break
        page.wait_for_timeout(100)
    else:
        return None, "unsupported_booking_provider"
    frame.locator("#searchDate").wait_for(state="visible", timeout=15000)
    observed_issuer = parse_qs(urlsplit(frame.url).query).get("issuerId", [])
    if expected_issuer is not None and observed_issuer != [expected_issuer]:
        raise GolfError("Golf Austria opened a different club's calendar than requested.")
    if requested_date:
        target_date = calendar_date.fromisoformat(requested_date)
        parsed = urlsplit(frame.url)
        params = parse_qs(parsed.query)
        if not params.get("issuerId"):
            raise GolfError("The calendar did not identify the selected club.")
        params["date"] = [target_date.strftime("%d.%m.%Y")]
        params["hour"] = [str(hour)]
        params["minute"] = ["0"]
        response = frame.goto(
            urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, urlencode(params, doseq=True), "")
            ),
            wait_until="domcontentloaded",
            timeout=45000,
        )
        if response is None or response.status >= 400:
            raise GolfError("Golf Austria could not load the requested date.")
        final_url = urlsplit(frame.url)
        if (
            final_url.hostname != "teetimes.golf.at"
            or final_url.path != "/mp1/search"
            or parse_qs(final_url.query).get("issuerId", []) != observed_issuer
        ):
            raise GolfError("Golf Austria redirected to a different club or unsupported calendar.")
        frame.locator("#searchDate").wait_for(state="visible", timeout=15000)
        displayed_value = frame.locator("#searchDate").input_value()
        displayed_date = None
        for pattern in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                displayed_date = datetime.strptime(displayed_value, pattern).date()
                break
            except ValueError:
                continue
        if displayed_date != target_date:
            raise GolfError("The calendar displayed a different date than requested.")
    return frame, "ok"


def availability(
    club_id,
    requested_date,
    state_dir=DEFAULT_STATE,
    *,
    duration_minutes=None,
    clubs_file=DEFAULT_CLUBS,
):
    from .calendar import parse_calendar

    calendar_date.fromisoformat(requested_date)
    clubs = configured_clubs(clubs_file)
    club = clubs.get(str(club_id))
    if club is None:
        raise ValueError(
            "Club is not configured. Add verified coordinates and metadata to the clubs file first."
        )
    with browser_session(state_dir) as context:
        page = context.pages[0] if context.pages else context.new_page()
        frame, status = open_calendar(
            page, club_id, requested_date, expected_issuer=club.get("calendar_issuer_id")
        )
        if frame is None:
            return {
                "status": status,
                "club_id": str(club_id),
                "club": club["name"],
                "date": requested_date,
                "source_url": calendar_url(club_id),
                "candidates": None,
                "message": "Golf Austria did not provide an accessible supported calendar; this is not evidence of no available times.",
            }
        return parse_calendar(
            frame.content(),
            club=club,
            requested_date=requested_date,
            source_url=calendar_url(club_id),
            duration_minutes=duration_minutes,
        )


def open_booking_browser(
    club_id,
    requested_date,
    state_dir=DEFAULT_STATE,
    *,
    timeout_seconds=900,
    clubs_file=DEFAULT_CLUBS,
):
    """Hand the calendar to the user. Never select or submit a booking."""
    import sys

    calendar_date.fromisoformat(requested_date)
    club = configured_clubs(clubs_file).get(str(club_id))
    if club is None:
        raise ValueError(
            "Club is not configured. Verify its metadata before opening a booking calendar."
        )
    with browser_session(state_dir, headed=True) as context:
        page = context.pages[0] if context.pages else context.new_page()
        frame, status = open_calendar(
            page, club_id, requested_date, expected_issuer=club.get("calendar_issuer_id")
        )
        if frame is None:
            return {"status": status, "club_id": str(club_id)}
        print(
            "The club calendar is open. You can select and book a time directly in the browser. Close it when finished.",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not page.is_closed():
            try:
                page.wait_for_timeout(500)
            except Exception:
                if page.is_closed():
                    break
                raise
        return {"status": "browser_handoff_complete", "booking_status": "not_verified"}


def inspect_calendar(club_id, state_dir=DEFAULT_STATE):
    """Return structural diagnostics without passwords, cookies or player names."""
    url = calendar_url(club_id)
    with browser_session(state_dir) as context:
        page = context.pages[0] if context.pages else context.new_page()
        club = configured_clubs().get(str(club_id), {})
        frame, status = open_calendar(page, club_id, expected_issuer=club.get("calendar_issuer_id"))
        result = {
            "club_id": str(club_id),
            "source_url": url,
            "checked_at": datetime.now(UTC).isoformat(),
        }
        if frame is None:
            return {**result, "status": status, "slots": None}
        structure = frame.evaluate("""() => ({
            forms: [...document.forms].map(f => ({
                name: f.name, method: f.method,
                action_path: new URL(f.action, location.href).pathname,
                fields: [...f.elements].map(e => ({name: e.name, type: e.type, id: e.id}))
            })),
            selects: [...document.querySelectorAll('select')].map(s => ({
                id: s.id, name: s.name, options_count: s.options.length
            })),
            calendar_cells: document.querySelectorAll('.teetime-box').length,
            frames: [...document.querySelectorAll('iframe')].map(f => {
                try {const u = new URL(f.src, location.href); return u.origin + u.pathname;}
                catch { return null; }
            })
        })""")
        return {**result, "status": "ok", "page_url": safe_url(frame.url), **structure}
