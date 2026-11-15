"""Check booking handoff identity/date without opening a browser."""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from golf_austria import portal
from golf_austria.browser import GolfError


class FakeFrame:
    def __init__(self, *, issuer="expected", displayed_date="16/11/2026", redirect_issuer=None):
        self.url = f"https://teetimes.golf.at/mp1/search?issuerId={issuer}&date=15.11.2026"
        self.date_control = MagicMock()
        self.date_control.input_value.return_value = displayed_date
        self.requested_urls = []
        self.redirect_issuer = redirect_issuer

    def locator(self, selector):
        if selector != "#searchDate":
            raise AssertionError(f"Unexpected control {selector}")
        return self.date_control

    def goto(self, url, **kwargs):
        self.requested_urls.append(url)
        self.url = url
        if self.redirect_issuer:
            self.url = f"https://teetimes.golf.at/mp1/search?issuerId={self.redirect_issuer}&date=16.11.2026"
        return SimpleNamespace(status=200)


def fake_page(frame):
    page = MagicMock()
    page.goto.return_value = SimpleNamespace(status=200)
    page.frames = [frame]
    page.locator.return_value.inner_text.return_value = "Authenticated calendar wrapper"
    page.locator.return_value.count.return_value = 0
    return page


@contextmanager
def fake_session(page):
    yield SimpleNamespace(pages=[page])


class CalendarHandoffTests(unittest.TestCase):
    def test_wrong_initial_club_is_rejected_before_date_navigation(self):
        frame = FakeFrame(issuer="different-club")
        with self.assertRaises(GolfError):
            portal.open_calendar(fake_page(frame), "318", "2026-11-16", expected_issuer="expected")
        self.assertEqual(frame.requested_urls, [])

    def test_club_change_after_date_redirect_is_rejected(self):
        frame = FakeFrame(redirect_issuer="different-club")
        with self.assertRaises(GolfError):
            portal.open_calendar(fake_page(frame), "318", "2026-11-16", expected_issuer="expected")

    def test_provider_date_fallback_is_not_a_successful_handoff(self):
        for displayed in ("17/11/2026", "", "not-a-date"):
            with self.subTest(displayed=displayed):
                frame = FakeFrame(displayed_date=displayed)
                with self.assertRaisesRegex(GolfError, "date"):
                    portal.open_calendar(
                        fake_page(frame), "318", "2026-11-16", expected_issuer="expected"
                    )

    def test_verified_date_and_club_allow_only_search_navigation(self):
        for displayed in ("16/11/2026", "16.11.2026", "2026-11-16"):
            with self.subTest(displayed=displayed):
                frame = FakeFrame(displayed_date=displayed)
                observed, status = portal.open_calendar(
                    fake_page(frame), "318", "2026-11-16", expected_issuer="expected"
                )
                self.assertIs(observed, frame)
                self.assertEqual(status, "ok")
                self.assertEqual(len(frame.requested_urls), 1)
                requested = urlsplit(frame.requested_urls[0])
                self.assertEqual(requested.path, "/mp1/search")
                self.assertEqual(parse_qs(requested.query)["date"], ["16.11.2026"])
                self.assertEqual(parse_qs(requested.query)["issuerId"], ["expected"])

    def test_booking_browser_uses_configured_club_identity(self):
        page = fake_page(FakeFrame(issuer="different-club"))
        with patch.object(
            portal, "configured_clubs", return_value={"318": {"calendar_issuer_id": "expected"}}
        ):
            with patch.object(portal, "browser_session", return_value=fake_session(page)):
                with self.assertRaises(GolfError):
                    portal.open_booking_browser("318", "2026-11-16")


if __name__ == "__main__":
    unittest.main()
