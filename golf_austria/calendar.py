"""Parse observed Golf Austria/Nexxchange calendar markup without making bookings.

Booking links can create an order even though they use GET. This module only
reads them and returns public identifier fields, never a checkout URL or a
player's identity. The caller must freshly resolve any explicitly chosen slot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo


class CalendarParseError(ValueError):
    """The page cannot establish availability for the requested date."""


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    contents: list = field(default_factory=list)

    def has(self, cls: str) -> bool:
        return cls in (self.attrs.get("class") or "").split()

    def walk(self):
        yield self
        for content in self.contents:
            if isinstance(content, _Node):
                yield from content.walk()

    def text(self, *, price=False) -> str:
        if self.tag in {"script", "style"}:
            return ""
        if price and (self.tag in {"del", "s", "strike"} or self.has("text-muted")):
            return ""
        return " ".join(
            content.text(price=price) if isinstance(content, _Node) else content
            for content in self.contents
        ).strip()


class _Tree(HTMLParser):
    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].contents.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].contents.append(data)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _one(query: dict, key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if len(values) == 1 else None


def _amount(text: str) -> float | None:
    # Match EUR amounts only, with either common decimal convention.
    match = re.search(r"(?:€|EUR)\s*(\d[\d.,]*)|\b(\d[\d.,]*)\s*(?:€|EUR)", text, re.I)
    if not match:
        return None
    amount = next(value for value in match.groups() if value is not None).rstrip(".,")
    if "," in amount and "." in amount:
        decimal = "," if amount.rfind(",") > amount.rfind(".") else "."
        amount = amount.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in amount:
        amount = amount.replace(",", ".")
    try:
        return float(amount)
    except ValueError:
        return None


def _source_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in {
        "www.golf.at",
        "teetimes.golf.at",
        "www.nexxchange.com",
    }:
        raise CalendarParseError("Calendar provenance must use an official HTTPS booking host.")
    # Never propagate sign-in hash, name, account ID, or any other query material.
    query = parse_qs(parts.query)
    safe = {
        key: query[key][0]
        for key in ("date", "hour", "minute", "courseId", "searchType")
        if len(query.get(key, [])) == 1
    }
    club_number = _one(query, "clubnr")
    if club_number and re.fullmatch(r"[0-9]+", club_number):
        safe["clubnr"] = club_number
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe), ""))


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise CalendarParseError("verified_at must include a timezone offset.")
    return parsed.isoformat()


def parse_calendar(
    html: str,
    *,
    club: dict,
    requested_date: str,
    source_url: str,
    verified_at: str | None = None,
    duration_minutes: int | None = None,
) -> dict:
    """Return dated candidates, anonymous tee-sheet slots and course notices.

    ``club`` supplies id/name/latitude/longitude and optional timezone. Round
    duration is a planning assumption, not supplied by the booking provider:
    by default 120 minutes for 9 holes or 270 for 18, with an explicit override
    allowed. Product length comes from the actual offered facet's 9/18-hole
    label. Unknown price is None. Current listed prices are per player and may
    still change at checkout. ``slots`` retains every observed tee-time row,
    including fully booked rows, with anonymous occupancy counts. No successful
    booking is implied by this result.
    """
    target = date.fromisoformat(requested_date)
    if duration_minutes is not None and (
        not isinstance(duration_minutes, int)
        or isinstance(duration_minutes, bool)
        or not 1 <= duration_minutes <= 720
    ):
        raise CalendarParseError("duration_minutes must be an integer from 1 to 720, or None.")
    source = _source_url(source_url)
    checked_at = _timestamp(verified_at)
    tree = _Tree()
    tree.feed(html)
    nodes = list(tree.root.walk())
    # Only use body text for specific error markers; do not return it or player data.
    text = _clean(tree.root.text())
    base = {
        "date": requested_date,
        "source_url": source,
        "verified_at": checked_at,
        "candidates": [],
        "slots": [],
        "notices": [],
    }
    if "Userdaten nicht vorhanden" in text or any(
        node.attrs.get("name") == "loginpassword" for node in nodes
    ):
        return {**base, "status": "authentication_required"}
    if "Kein Zugriff auf diesen Bereich" in text:
        return {**base, "status": "access_denied"}
    inputs = [node for node in nodes if node.attrs.get("id") == "searchDate"]
    if len(inputs) != 1:
        raise CalendarParseError("The calendar date control is missing or ambiguous.")
    actual = None
    for pattern in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            actual = datetime.strptime(inputs[0].attrs.get("value") or "", pattern).date()
            break
        except ValueError:
            pass
    if actual != target:
        raise CalendarParseError("The displayed calendar date does not match the requested date.")

    products = {}
    for node in nodes:
        if node.tag != "a":
            continue
        query = parse_qs(urlsplit(node.attrs.get("href") or "").query)
        facet_id = _one(query, "facetId")
        if not facet_id:
            continue
        names = [child for child in node.walk() if child.has("facet-name")]
        if not names:
            continue
        label = _clean(names[0].text())
        holes_match = re.search(r"\b(9|18)\s*[- ]?\s*(?:Loch|holes?)\b", label, re.I)
        prices = [child for child in node.walk() if child.has("price")]
        price = _amount(prices[0].text(price=True)) if prices else None
        products[facet_id] = {
            "id": facet_id,
            "name": label,
            "holes": int(holes_match.group(1)) if holes_match else None,
            "price_eur": price,
            "currency": "EUR" if price is not None else None,
        }

    course_buttons = [node for node in nodes if node.tag == "button" and node.has("course-name")]
    course = _clean(course_buttons[0].text()) if len(course_buttons) == 1 else None
    notices = list(dict.fromkeys(_clean(node.text()) for node in nodes if node.has("message-info")))
    rows = [node for node in nodes if node.has("teetime-box")]
    if not rows and not any(
        marker in text.lower()
        for marker in (
            "couldn't find anything matching your criteria",
            "keine treffer",
            "next tee time is available",
            "nächste startzeit",
        )
    ):
        raise CalendarParseError(
            "No recognized tee-time rows or explicit empty-calendar message found."
        )

    expected_issuer = club.get("calendar_issuer_id")
    if expected_issuer:
        observed_issuers = set()
        for node in nodes:
            if node.tag != "a":
                continue
            parts = urlsplit(node.attrs.get("href") or "")
            if parts.path not in {
                "/mp1/search",
                "/mp1/checkout/book",
                "/widgets-web/booking/submitSelectProductForm",
            }:
                continue
            issuer = _one(parse_qs(parts.query), "issuerId")
            if issuer:
                observed_issuers.add(issuer)
        source_issuer = _one(parse_qs(urlsplit(source_url).query), "issuerId")
        if source_issuer:
            observed_issuers.add(source_issuer)
        if observed_issuers - {str(expected_issuer)}:
            raise CalendarParseError("The calendar issuer does not match the configured club.")
        if rows and not observed_issuers:
            raise CalendarParseError(
                "The calendar issuer cannot be verified for these tee-time rows."
            )

    expected_courses = club.get("calendar_course_names")
    if expected_courses:
        allowed_courses = {_clean(name).casefold() for name in expected_courses}
        observed_course = _clean(course or "").casefold()
        # A verified empty calendar can show N/A instead of selecting a course.
        if rows or observed_course not in {"", "n/a"}:
            if observed_course not in allowed_courses:
                raise CalendarParseError("The displayed course does not match the configured club.")

    candidates = []
    slots = []
    tz = ZoneInfo(club.get("timezone", "Europe/Vienna"))
    for row in rows:
        time_nodes = [node for node in row.walk() if node.has("teetime-time")]
        if len(time_nodes) != 1:
            raise CalendarParseError("A tee-time row has no unambiguous start time.")
        time_match = re.fullmatch(r"(\d{1,2}):(\d{2})", _clean(time_nodes[0].text()))
        if time_match is None:
            raise CalendarParseError("Unrecognized tee-time clock format.")
        start = datetime(
            target.year,
            target.month,
            target.day,
            int(time_match.group(1)),
            int(time_match.group(2)),
            tzinfo=tz,
        )
        grouped = {}
        booked_players = 0
        unknown_players = 0
        player_cells = [node for node in row.walk() if node.has("player-box")]
        for node in player_cells:
            is_booked = node.has("booked")
            is_free = node.has("none")
            if is_booked:
                if is_free or node.has("disabled") or node.attrs.get("aria-disabled") == "true":
                    unknown_players += 1
                else:
                    booked_players += 1
                continue
            if (
                is_free
                and node.tag == "a"
                and not node.has("disabled")
                and node.attrs.get("aria-disabled") != "true"
            ):
                parts = urlsplit(node.attrs.get("href") or "")
                if parts.netloc and parts.netloc not in {
                    "teetimes.golf.at",
                    "www.nexxchange.com",
                }:
                    raise CalendarParseError(
                        "A tee-time booking link points to an unexpected host."
                    )
                if parts.path not in {
                    "/mp1/checkout/book",
                    "/widgets-web/booking/submitSelectProductForm",
                }:
                    raise CalendarParseError(
                        "A free player slot uses an unrecognized booking action."
                    )
                query = parse_qs(parts.query)
                tee_id, facet_id, issuer_id = (
                    _one(query, key) for key in ("teeTimeId", "facetId", "issuerId")
                )
                if (
                    not tee_id
                    or not facet_id
                    or not issuer_id
                    or not re.fullmatch(r"-?\d+", tee_id)
                    or not facet_id.isdigit()
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", issuer_id)
                ):
                    raise CalendarParseError(
                        "A booking link lacks its expected public slot identifiers."
                    )
                key = (tee_id, facet_id, issuer_id)
                grouped.setdefault(key, []).append(node)
                continue
            unknown_players += 1
        if row.has("fullybooked") and grouped:
            raise CalendarParseError("A row contradicts its fully-booked status.")
        slots.append(
            {
                "start": start.isoformat(),
                "available_players": (
                    sum(len(seats) for seats in grouped.values()) if len(grouped) <= 1 else None
                ),
                "booked_players": booked_players,
                "unknown_players": unknown_players,
                "occupancy_known": (
                    bool(player_cells) and unknown_players == 0 and len(grouped) <= 1
                ),
            }
        )
        for (tee_id, facet_id, issuer_id), seats in grouped.items():
            if len(seats) > 4:
                raise CalendarParseError(
                    "Unexpected player count; calendar markup may have changed."
                )
            product = products.get(facet_id)
            if not product or product["holes"] is None:
                raise CalendarParseError(
                    "The slot's offered product or 9/18-hole length cannot be established."
                )
            seat_prices = {_amount(seat.text(price=True)) for seat in seats}
            if len(seat_prices) > 1:
                raise CalendarParseError(
                    "The available seats show differing prices; select a specific product first."
                )
            direct_price = next(iter(seat_prices))
            price = direct_price if direct_price is not None else product["price_eur"]
            candidates.append(
                {
                    "id": f"{club['id']}:{tee_id}:{facet_id}",
                    "club": club["name"],
                    "club_id": str(club["id"]),
                    "course": course,
                    "product": product["name"],
                    "start": start.isoformat(),
                    "holes": product["holes"],
                    "duration_minutes": duration_minutes
                    if duration_minutes is not None
                    else (120 if product["holes"] == 9 else 270),
                    "duration_source": "user_override"
                    if duration_minutes is not None
                    else "default_planning_assumption",
                    "available_players": len(seats),
                    "price_eur": price,
                    "currency": "EUR" if price is not None else None,
                    "price_basis": "per_player_displayed_before_checkout",
                    "latitude": club.get("latitude"),
                    "longitude": club.get("longitude"),
                    "source_url": source,
                    "verified_at": checked_at,
                    "course_notices": list(notices),
                    "booking_reference": {
                        "tee_time_id": tee_id,
                        "facet_id": facet_id,
                        "issuer_id": issuer_id,
                    },
                }
            )
    return {
        **base,
        "status": "ok",
        "course": course,
        "products": list(products.values()),
        "notices": notices,
        "calendar_slots_seen": len(rows),
        "slots": slots,
        "candidates": candidates,
    }
