import json
import unittest

from golf_austria.calendar import CalendarParseError, parse_calendar

CLUB = {
    "id": "123",
    "name": "Synthetic Club",
    "latitude": 48.1,
    "longitude": 16.4,
    "course_holes": 9,
}


def html_fixture(
    price='<del class="text-muted">€29.00</del> €22.00', label="GF WT 9 Loch", day="16/11/2026"
):
    seat = '<a class="player-box none" href="/mp1/checkout/book?issuerId=public123&amp;teeTimeId=-100&amp;facetId=5"><i></i></a>'
    return f'''<html><body><input id="searchDate" value="{day}">
    <button class="course-name">Synthetic Course</button>
    <a href="/mp1/search?facetId=5"><span class="facet-name">{label}</span><span class="price">{price}</span></a>
    <div class="message-info"><h3>Frost</h3>Check the club's opening notice.</div>
    <div class="teetime-box"><div class="teetime-time">09:10</div>{seat}{seat}
      <div class="player-box booked">PRIVATE PLAYER NAME</div><div class="player-box booked">ANOTHER PRIVATE NAME</div>
    </div></body></html>'''


def parse(html, **kwargs):
    club = kwargs.pop("club", CLUB)
    source_url = kwargs.pop(
        "source_url",
        "https://teetimes.golf.at/mp1/search?date=16.11.2026&hash=SECRET&bookAs=PRIVATE",
    )
    return parse_calendar(
        html,
        club=club,
        requested_date="2026-11-16",
        source_url=source_url,
        verified_at="2026-11-15T23:00:00+01:00",
        **kwargs,
    )


class CalendarTests(unittest.TestCase):
    def test_exact_free_seats_current_price_and_no_player_or_secret_leak(self):
        result = parse(html_fixture())
        candidate = result["candidates"][0]
        self.assertEqual(candidate["available_players"], 2)
        self.assertEqual(candidate["price_eur"], 22)
        self.assertEqual(candidate["holes"], 9)
        self.assertEqual(candidate["start"], "2026-11-16T09:10:00+01:00")
        self.assertEqual(candidate["booking_reference"]["tee_time_id"], "-100")
        self.assertEqual(len(result["notices"]), 1)
        self.assertEqual(candidate["course_notices"], result["notices"])
        serialized = json.dumps(result)
        for secret in ("PRIVATE", "SECRET", "checkout/book", "PLAYER NAME"):
            self.assertNotIn(secret, serialized)

    def test_product_length_comes_from_facet_not_course(self):
        self.assertEqual(parse(html_fixture(label="18 Loch"))["candidates"][0]["holes"], 18)
        with self.assertRaises(CalendarParseError):
            parse(html_fixture(label="Greenfee"))

    def test_default_round_duration_depends_on_offered_holes(self):
        nine = parse(html_fixture())["candidates"][0]
        eighteen = parse(html_fixture(label="18 Loch"))["candidates"][0]
        self.assertEqual(nine["duration_minutes"], 120)
        self.assertEqual(eighteen["duration_minutes"], 270)
        self.assertEqual(eighteen["duration_source"], "default_planning_assumption")
        overridden = parse(html_fixture(label="18 Loch"), duration_minutes=240)["candidates"][0]
        self.assertEqual(overridden["duration_minutes"], 240)
        self.assertEqual(overridden["duration_source"], "user_override")

    def test_invalid_duration_override_is_rejected(self):
        for duration in (0, -1, 721, True, 120.5, "120"):
            with self.subTest(duration=duration), self.assertRaises(CalendarParseError):
                parse(html_fixture(), duration_minutes=duration)

    def test_unknown_price_stays_unknown(self):
        self.assertIsNone(parse(html_fixture(price=""))["candidates"][0]["price_eur"])

    def test_date_mismatch_fails(self):
        with self.assertRaises(CalendarParseError):
            parse(html_fixture(day="17/11/2026"))

    def test_no_rows_only_accepted_with_explicit_empty_state(self):
        date = '<input id="searchDate" value="16/11/2026">'
        result = parse(date + "Sorry, we couldn't find anything matching your criteria.")
        self.assertEqual(result["candidates"], [])
        with self.assertRaises(CalendarParseError):
            parse(date + "Loading…")

    def test_authentication_and_access_errors_are_distinct(self):
        self.assertEqual(parse("Userdaten nicht vorhanden")["status"], "authentication_required")
        self.assertEqual(parse("Kein Zugriff auf diesen Bereich")["status"], "access_denied")

    def test_numeric_club_number_preserved_in_safe_source(self):
        result = parse(
            html_fixture(),
            source_url="https://www.golf.at/tt/startzeiten.asp?clubnr=123&hash=SECRET",
        )
        self.assertEqual(result["source_url"], "https://www.golf.at/tt/startzeiten.asp?clubnr=123")
        result = parse(
            html_fixture(), source_url="https://www.golf.at/tt/startzeiten.asp?clubnr=SECRET"
        )
        self.assertNotIn("SECRET", result["source_url"])

    def test_different_club_or_course_is_rejected(self):
        configured = {
            **CLUB,
            "calendar_issuer_id": "public123",
            "calendar_course_names": ["Synthetic Course"],
        }
        self.assertEqual(len(parse(html_fixture(), club=configured)["candidates"]), 1)
        with self.assertRaisesRegex(CalendarParseError, "issuer"):
            parse(
                html_fixture().replace("issuerId=public123", "issuerId=wrongclub"), club=configured
            )
        with self.assertRaisesRegex(CalendarParseError, "course"):
            parse(html_fixture().replace("Synthetic Course", "Different Course"), club=configured)

    def test_empty_calendar_allows_na_course_but_rejects_wrong_issuer(self):
        configured = {
            **CLUB,
            "calendar_issuer_id": "public123",
            "calendar_course_names": ["Synthetic Course"],
        }
        html = """<input id="searchDate" value="16/11/2026">
        <button class="course-name">N/A</button>
        <a href="/mp1/search?issuerId=public123&date=17.11.2026">Next day</a>
        Sorry, we couldn't find anything matching your criteria."""
        self.assertEqual(parse(html, club=configured)["candidates"], [])
        with self.assertRaisesRegex(CalendarParseError, "issuer"):
            parse(html.replace("issuerId=public123", "issuerId=wrongclub"), club=configured)


if __name__ == "__main__":
    unittest.main()
