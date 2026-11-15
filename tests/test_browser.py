"""Session persistence and error handling, using synthetic cookies only."""

import json
import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from playwright.sync_api import Error as PlaywrightError

from golf_austria import browser

COOKIE = {"name": "session", "value": "synthetic-secret", "domain": "www.golf.at", "path": "/"}


class FakeContext:
    def __init__(self):
        self.pages = []
        self.listeners = {}
        self.restored = None
        self.snapshot = [COOKIE]
        self.snapshot_error = None
        self.closed = False
        self.save_calls = 0

    def set_default_timeout(self, value):
        pass

    def on(self, event, callback):
        self.listeners[event] = callback

    def add_cookies(self, cookies):
        self.restored = cookies

    def cookies(self):
        self.save_calls += 1
        if self.snapshot_error:
            raise self.snapshot_error
        return self.snapshot

    def close(self):
        if not self.closed:
            self.closed = True
            self.pages = []
            if "close" in self.listeners:
                self.listeners["close"]()


def fake_playwright(context):
    manager = MagicMock()
    manager.__enter__.return_value.chromium.launch_persistent_context.return_value = context
    return patch.object(browser, "sync_playwright", return_value=manager)


@contextmanager
def yield_context(context):
    yield context


class BrowserSessionTests(unittest.TestCase):
    def test_session_cookies_restore_and_save_atomically_with_private_permissions(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "cookies.json").write_text(json.dumps([COOKIE]))
            context = FakeContext()
            result = {}
            with fake_playwright(context), browser.browser_session(state, session_result=result):
                self.assertEqual(context.restored, [COOKIE])
                context.snapshot = [{**COOKIE, "value": "rotated-synthetic-secret"}]
            self.assertTrue(result["session_saved"])
            self.assertTrue(context.closed)
            self.assertEqual(json.loads((state / "cookies.json").read_text()), context.snapshot)
            self.assertEqual((state / "cookies.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                {p.name for p in state.iterdir()}, {"browser", "browser.lock", "cookies.json"}
            )

    def test_invalid_state_does_not_leak_or_overwrite_the_original_snapshot(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            secret_state = '{"sensitive": "synthetic-secret"}'
            (state / "cookies.json").write_text(secret_state)
            context = FakeContext()
            with fake_playwright(context), self.assertRaises(browser.GolfError) as caught:
                with browser.browser_session(state):
                    self.fail("Invalid state must not start an operation")
            self.assertNotIn("synthetic-secret", str(caught.exception))
            self.assertEqual((state / "cookies.json").read_text(), secret_state)
            self.assertEqual(context.save_calls, 0)
            self.assertTrue(context.closed)

    def test_browser_cookie_validation_error_is_sanitized(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "cookies.json").write_text(json.dumps([COOKIE]))
            context = FakeContext()
            context.add_cookies = MagicMock(side_effect=PlaywrightError("invalid synthetic-secret"))
            with fake_playwright(context), self.assertRaises(browser.GolfError) as caught:
                with browser.browser_session(state):
                    self.fail("Invalid cookie must not be used")
            self.assertNotIn("synthetic-secret", str(caught.exception))
            self.assertEqual(context.save_calls, 0)
            self.assertTrue(context.closed)

    def test_snapshot_failure_preserves_existing_state_and_reports_failure(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            old = json.dumps([COOKIE])
            (state / "cookies.json").write_text(old)
            context = FakeContext()
            context.snapshot_error = PlaywrightError("error containing synthetic-secret")
            result = {}
            with fake_playwright(context), self.assertRaises(browser.GolfError) as caught:
                with browser.browser_session(state, session_result=result):
                    pass
            self.assertFalse(result["session_saved"])
            self.assertNotIn("synthetic-secret", str(caught.exception))
            self.assertEqual((state / "cookies.json").read_text(), old)
            self.assertTrue(context.closed)

    def test_failed_atomic_replace_preserves_previous_snapshot_and_removes_temp_file(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            old = json.dumps([COOKIE])
            (state / "cookies.json").write_text(old)
            context = FakeContext()
            with (
                fake_playwright(context),
                patch.object(browser.os, "replace", side_effect=OSError("disk failure")),
            ):
                with self.assertRaises(browser.GolfError):
                    with browser.browser_session(state):
                        context.snapshot = [{**COOKIE, "value": "rotated-synthetic-secret"}]
            self.assertEqual((state / "cookies.json").read_text(), old)
            self.assertEqual(
                {p.name for p in state.iterdir()}, {"browser", "browser.lock", "cookies.json"}
            )

    def test_concurrent_browser_session_is_rejected_before_launch(self):
        with TemporaryDirectory() as directory:
            context = FakeContext()
            with fake_playwright(context), browser.browser_session(directory):
                with self.assertRaisesRegex(browser.GolfError, "already running"):
                    with browser.browser_session(directory):
                        self.fail("A second session must not modify the profile")

    def test_cleanup_failure_does_not_replace_an_operation_failure(self):
        with TemporaryDirectory() as directory:
            context = FakeContext()
            context.snapshot_error = OSError("snapshot write failed")
            with fake_playwright(context), self.assertRaisesRegex(ValueError, "original operation"):
                with browser.browser_session(directory):
                    raise ValueError("original operation")
            self.assertTrue(context.closed)

    def test_unrelated_cleanup_failure_is_not_swallowed(self):
        with TemporaryDirectory() as directory:
            context = FakeContext()
            context.snapshot_error = RuntimeError("unexpected library bug")
            with (
                fake_playwright(context),
                self.assertRaisesRegex(RuntimeError, "unexpected library bug"),
            ):
                with browser.browser_session(directory):
                    pass
            self.assertTrue(context.closed)

    def test_manual_close_does_not_claim_new_session_was_saved(self):
        with TemporaryDirectory() as directory:
            state = Path(directory)
            old = json.dumps([COOKIE])
            (state / "cookies.json").write_text(old)
            context = FakeContext()
            page = MagicMock()
            page.goto.return_value = SimpleNamespace(status=200)
            page.is_closed.side_effect = lambda: context.close() or True
            context.pages = [page]
            with fake_playwright(context):
                result = browser.login(state)
            self.assertEqual(result["status"], "browser_closed")
            self.assertFalse(result["session_saved"])
            self.assertIsNone(result["authenticated"])
            self.assertEqual((state / "cookies.json").read_text(), old)

    def test_umask_is_restored_when_lock_file_cannot_be_opened(self):
        with TemporaryDirectory() as directory:
            previous = os.umask(0o022)
            try:
                with patch.object(Path, "open", side_effect=PermissionError("lock denied")):
                    with self.assertRaises(PermissionError):
                        with browser.browser_session(directory):
                            self.fail("Lock failure must prevent browser launch")
                actual = os.umask(0o022)
                self.assertEqual(actual, 0o022)
            finally:
                os.umask(previous)

    def test_http_failure_is_not_reported_as_signed_out(self):
        context = FakeContext()
        page = MagicMock()
        page.goto.return_value = SimpleNamespace(status=503)
        context.pages = [page]
        with patch.object(browser, "browser_session", return_value=yield_context(context)):
            with self.assertRaisesRegex(browser.GolfError, "HTTP 503"):
                browser.session_status()
        page.locator.assert_not_called()

    def test_unknown_200_response_is_unavailable_not_signed_out(self):
        context = FakeContext()
        page = MagicMock()
        page.goto.return_value = SimpleNamespace(status=200)
        page.locator.return_value.count.return_value = 0
        page.url = "https://www.golf.at/maintenance?irrelevant=1"
        context.pages = [page]
        with patch.object(browser, "browser_session", return_value=yield_context(context)):
            result = browser.session_status()
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["authenticated"])
        self.assertNotIn("?", result["url"])


if __name__ == "__main__":
    unittest.main()
