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


def slot_fixture(cells, *, row_class=""):
    return f"""<html><body><input id="searchDate" value="16/11/2026">
    <button class="course-name">Synthetic Course</button>
    <a href="/mp1/search?facetId=5"><span class="facet-name">GF WT 9 Loch</span><span class="price">€22.00</span></a>
    <div class="teetime-box {row_class}"><div class="teetime-time">10:20</div>{cells}</div>
    </body></html>"""


def free_seat(*, disabled=False, facet_id=5, tee_time_id=-100):
    state = (
        ' class="player-box none disabled" aria-disabled="true"'
        if disabled
        else ' class="player-box none"'
    )
    return f'<a{state} href="/mp1/checkout/book?issuerId=public123&amp;teeTimeId={tee_time_id}&amp;facetId={facet_id}"><i></i></a>'


def booked_seat():
    return '<div class="player-box booked">PRIVATE PLAYER NAME</div>'


def multi_product_slot_fixture(cells):
    return f"""<html><body><input id="searchDate" value="16/11/2026">
    <button class="course-name">Synthetic Course</button>
    <a href="/mp1/search?facetId=5"><span class="facet-name">GF WT 9 Loch</span><span class="price">€22.00</span></a>
    <a href="/mp1/search?facetId=6"><span class="facet-name">GF WT 9 Loch Alternative</span><span class="price">€25.00</span></a>
    <div class="teetime-box"><div class="teetime-time">10:20</div>{cells}</div>
    </body></html>"""


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
        slot = result["slots"][0]
        self.assertEqual(candidate["available_players"], 2)
        self.assertEqual(slot["start"], "2026-11-16T09:10:00+01:00")
        self.assertEqual(slot["available_players"], 2)
        self.assertEqual(slot["booked_players"], 2)
        self.assertEqual(slot["unknown_players"], 0)
        self.assertTrue(slot["occupancy_known"])
        self.assertEqual(candidate["price_eur"], 22)
        self.assertEqual(candidate["holes"], 9)
        self.assertEqual(candidate["start"], "2026-11-16T09:10:00+01:00")
        self.assertEqual(candidate["booking_reference"]["tee_time_id"], "-100")
        self.assertEqual(len(result["notices"]), 1)
        self.assertEqual(candidate["course_notices"], result["notices"])
        serialized = json.dumps(result)
        for secret in ("PRIVATE", "SECRET", "checkout/book", "PLAYER NAME"):
            self.assertNotIn(secret, serialized)

    def test_slot_occupancy_preserves_empty_partial_and_full_rows(self):
        empty = parse(slot_fixture(free_seat() * 4))
        self.assertEqual(empty["candidates"][0]["available_players"], 4)
        self.assertEqual(
            empty["slots"],
            [
                {
                    "start": "2026-11-16T10:20:00+01:00",
                    "available_players": 4,
                    "booked_players": 0,
                    "unknown_players": 0,
                    "occupancy_known": True,
                }
            ],
        )

        partial = parse(slot_fixture(free_seat() * 2 + booked_seat() * 2))
        self.assertEqual(partial["slots"][0]["available_players"], 2)
        self.assertEqual(partial["slots"][0]["booked_players"], 2)
        self.assertTrue(partial["slots"][0]["occupancy_known"])

        full = parse(slot_fixture(booked_seat() * 4, row_class="fullybooked"))
        self.assertEqual(full["candidates"], [])
        self.assertEqual(full["slots"][0]["available_players"], 0)
        self.assertEqual(full["slots"][0]["booked_players"], 4)
        self.assertTrue(full["slots"][0]["occupancy_known"])

    def test_unknown_player_cells_do_not_become_available_or_booked(self):
        result = parse(
            slot_fixture(
                free_seat()
                + free_seat(disabled=True)
                + booked_seat()
                + '<div class="player-box booked disabled">DISABLED PLAYER NAME</div>'
                + '<span class="player-box">Unknown</span>'
            )
        )
        self.assertEqual(result["slots"][0]["available_players"], 1)
        self.assertEqual(result["slots"][0]["booked_players"], 1)
        self.assertEqual(result["slots"][0]["unknown_players"], 3)
        self.assertFalse(result["slots"][0]["occupancy_known"])
        serialized = json.dumps(result)
        for secret in ("PRIVATE", "DISABLED", "Unknown", "checkout/book"):
            self.assertNotIn(secret, serialized)

    def test_fully_booked_row_with_free_cell_remains_contradictory(self):
        with self.assertRaisesRegex(CalendarParseError, "fully-booked"):
            parse(slot_fixture(booked_seat() * 3 + free_seat(), row_class="fullybooked"))

    def test_contradictory_booked_and_free_cell_is_unknown_and_not_a_candidate(self):
        contradictory = free_seat().replace(
            'class="player-box none"', 'class="player-box none booked"'
        )
        result = parse(slot_fixture(contradictory))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            result["slots"][0],
            {
                "start": "2026-11-16T10:20:00+01:00",
                "available_players": 0,
                "booked_players": 0,
                "unknown_players": 1,
                "occupancy_known": False,
            },
        )

    def test_row_without_player_cells_has_unknown_occupancy(self):
        result = parse(slot_fixture(""))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["slots"][0]["available_players"], 0)
        self.assertEqual(result["slots"][0]["booked_players"], 0)
        self.assertEqual(result["slots"][0]["unknown_players"], 0)
        self.assertFalse(result["slots"][0]["occupancy_known"])

    def test_multiple_product_groups_do_not_look_like_one_physical_capacity(self):
        result = parse(
            multi_product_slot_fixture(
                free_seat(facet_id=5, tee_time_id=-100) * 2
                + free_seat(facet_id=6, tee_time_id=-101) * 2
            )
        )
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(
            sorted(candidate["available_players"] for candidate in result["candidates"]),
            [2, 2],
        )
        self.assertEqual(result["slots"][0]["available_players"], None)
        self.assertEqual(result["slots"][0]["booked_players"], 0)
        self.assertEqual(result["slots"][0]["unknown_players"], 0)
        self.assertFalse(result["slots"][0]["occupancy_known"])

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
        authentication = parse("Userdaten nicht vorhanden")
        self.assertEqual(authentication["status"], "authentication_required")
        self.assertEqual(authentication["slots"], [])
        access_denied = parse("Kein Zugriff auf diesen Bereich")
        self.assertEqual(access_denied["status"], "access_denied")
        self.assertEqual(access_denied["slots"], [])

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
