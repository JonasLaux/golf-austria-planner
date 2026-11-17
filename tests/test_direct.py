"""Direct upstream requests with synthetic HTML and mocked HTTP only."""

import io
import unittest
from email.message import Message
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from golf_austria import direct
from golf_austria.browser import GolfError
from golf_austria.calendar import CalendarParseError

CLUB = {
    "id": "123",
    "name": "Synthetic Club",
    "calendar_issuer_id": "public123",
    "calendar_course_names": ["Synthetic Course"],
}
HTML = b"""<input id="searchDate" value="16/11/2026">
<button class="course-name">Synthetic Course</button>
<a href="/mp1/search?issuerId=public123&amp;facetId=5">
  <span class="facet-name">9 holes</span><span class="price">EUR 22.00</span>
</a>
<div class="teetime-box"><div class="teetime-time">10:00</div>
  <a class="player-box none" href="/mp1/checkout/book?issuerId=public123&amp;teeTimeId=-100&amp;facetId=5"></a>
  <div class="player-box booked">PRIVATE PLAYER NAME</div>
</div>
<div class="teetime-box fullybooked"><div class="teetime-time">10:10</div>
  <div class="player-box booked">ANOTHER PRIVATE PLAYER</div>
</div>"""


class Response(io.BytesIO):
    def __init__(self, url, body, content_type="text/html; charset=utf-8"):
        super().__init__(body)
        self.url = url
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class DirectCalendarTests(unittest.TestCase):
    def setUp(self):
        self.club = dict(CLUB)
        self.body = HTML
        self.content_type = "text/html; charset=utf-8"
        self.config = self.enterContext(
            patch.object(direct, "configured_clubs", return_value={"123": self.club})
        )
        self.opener = MagicMock()
        self.opener.open.side_effect = lambda request, **kwargs: Response(
            request.full_url, self.body, self.content_type
        )
        self.build = self.enterContext(
            patch.object(direct, "build_opener", return_value=self.opener)
        )

    def test_single_public_get_returns_anonymous_full_sheet_and_upstream_parameters(self):
        result = direct.availability("123", "2026-11-16", hour=10, minute=20)
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            (parsed.scheme, parsed.netloc, parsed.path),
            ("https", "teetimes.golf.at", "/mp1/search"),
        )
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "issuerId": ["public123"],
                "date": ["16.11.2026"],
                "hour": ["10"],
                "minute": ["20"],
                "searchType": ["teeTime"],
                "restricted": ["true"],
            },
        )
        self.assertIsNone(request.get_header("Cookie"))
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 30)
        self.assertEqual(result["transport"], "direct_http")
        self.assertEqual(result["authentication"], "public")
        self.assertEqual(result["query"]["hour"], 10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(len(result["slots"]), 2)
        self.assertEqual(result["slots"][1]["booked_players"], 1)
        self.assertNotIn("PRIVATE", str(result))
        self.assertNotIn("checkout/book", str(result))

    def test_incomplete_mapping_does_not_request_or_claim_an_empty_calendar(self):
        for missing in ("calendar_issuer_id", "calendar_course_names"):
            with self.subTest(missing=missing):
                self.config.return_value = {
                    "123": {key: value for key, value in CLUB.items() if key != missing}
                }
                result = direct.availability("123", "2026-11-16")
                self.assertEqual(result["status"], "calendar_requires_mapping")
                self.assertIsNone(result["candidates"])
                self.assertIsNone(result["slots"])
        self.build.assert_not_called()

    def test_invalid_inputs_fail_before_network_access(self):
        cases = (
            (("bad-id", "2026-11-16"), {}),
            (("123", "not-a-date"), {}),
            (("999", "2026-11-16"), {}),
            (("123", "2026-11-16"), {"hour": 24}),
            (("123", "2026-11-16"), {"hour": True}),
            (("123", "2026-11-16"), {"minute": -1}),
            (("123", "2026-11-16"), {"duration_minutes": 0}),
        )
        for args, kwargs in cases:
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(ValueError):
                direct.availability(*args, **kwargs)
        self.build.assert_not_called()

    def test_untrusted_mapping_values_do_not_enter_requests(self):
        for key, value in (
            ("calendar_issuer_id", "public123&hash=SECRET"),
            ("calendar_course_names", "Synthetic Course"),
        ):
            with self.subTest(key=key):
                self.config.return_value = {"123": {**CLUB, key: value}}
                with self.assertRaises(ValueError):
                    direct.availability("123", "2026-11-16")
        self.build.assert_not_called()

    def test_every_redirect_is_refused_without_following_checkout(self):
        handler = direct._NoRedirect()
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                target = "https://teetimes.golf.at/mp1/checkout/book?hash=SECRET"
                self.assertIsNone(
                    handler.redirect_request(None, None, status, "redirect", {}, target)
                )
                self.opener.open.side_effect = HTTPError(
                    target, status, "SECRET", {"Location": target}, io.BytesIO()
                )
                with self.assertRaisesRegex(GolfError, "no redirect was followed") as caught:
                    direct.availability("123", "2026-11-16")
                self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(self.opener.open.call_count, 5)

    def test_http_authentication_denial_and_server_error_are_distinct(self):
        for code, status in ((401, "authentication_required"), (403, "access_denied")):
            with self.subTest(code=code):
                self.opener.open.side_effect = HTTPError(
                    direct.CALENDAR_URL, code, "SECRET", {}, io.BytesIO()
                )
                result = direct.availability("123", "2026-11-16")
                self.assertEqual(result["status"], status)
                self.assertIsNone(result["slots"])
                self.assertNotIn("SECRET", str(result))
        self.opener.open.side_effect = HTTPError(
            direct.CALENDAR_URL, 503, "SECRET", {}, io.BytesIO()
        )
        with self.assertRaisesRegex(GolfError, "HTTP 503"):
            direct.availability("123", "2026-11-16")

    def test_response_size_content_type_and_network_errors_are_sanitized(self):
        with patch.object(direct, "MAX_HTML_BYTES", 20):
            with self.assertRaisesRegex(GolfError, "unexpectedly large"):
                direct.availability("123", "2026-11-16")
        self.content_type = "application/json"
        with self.assertRaisesRegex(GolfError, "not HTML"):
            direct.availability("123", "2026-11-16")
        self.opener.open.side_effect = URLError("SECRET")
        with self.assertRaisesRegex(GolfError, "Could not read") as caught:
            direct.availability("123", "2026-11-16")
        self.assertNotIn("SECRET", str(caught.exception))

    def test_date_course_and_issuer_validation_are_preserved(self):
        for original, replacement in (
            (b"16/11/2026", b"17/11/2026"),
            (b"Synthetic Course", b"Other Course"),
            (b"issuerId=public123", b"issuerId=wrongclub"),
        ):
            with self.subTest(replacement=replacement):
                self.body = HTML.replace(original, replacement)
                with self.assertRaises(CalendarParseError):
                    direct.availability("123", "2026-11-16")


if __name__ == "__main__":
    unittest.main()
