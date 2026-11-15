"""Browser sessions: credentials stay in the dedicated browser profile."""

import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://www.golf.at/mygolf/login"


def default_state_directory() -> Path:
    """Keep account state out of source trees and installed package directories."""
    override = os.environ.get("GOLF_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    xdg_state = os.environ.get("XDG_STATE_HOME")
    state_home = (
        Path(xdg_state)
        if xdg_state and Path(xdg_state).is_absolute()
        else Path.home() / ".local" / "state"
    )
    return state_home / "golf-austria"


DEFAULT_STATE = default_state_directory()


class GolfError(Exception):
    """An actionable error safe to show without HTTP bodies or credentials."""


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _restore_cookies(context, cookies_file: Path):
    if not cookies_file.exists():
        return
    try:
        cookies_file.chmod(0o600)
        with cookies_file.open(encoding="utf-8") as stream:
            raw = stream.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("State too large")
        cookies = json.loads(raw)
        if not isinstance(cookies, list) or any(
            not isinstance(cookie, dict)
            or not isinstance(cookie.get("name"), str)
            or not isinstance(cookie.get("value"), str)
            for cookie in cookies
        ):
            raise ValueError("Invalid cookie list")
    except OSError:
        raise GolfError("Saved golf session data could not be read.") from None
    except (ValueError, UnicodeError):
        raise GolfError(
            "Saved golf session data is invalid. Move cookies.json aside and sign in again."
        ) from None
    try:
        context.add_cookies(cookies)
    except (PlaywrightError, TypeError, ValueError):
        # Playwright validation errors may include the rejected cookie values.
        raise GolfError(
            "Saved golf cookies could not be restored. Move cookies.json aside and sign in again."
        ) from None


def _save_cookies(context, cookies_file: Path):
    cookies = context.cookies()
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=cookies_file.parent, delete=False
        ) as saved:
            temporary_path = Path(saved.name)
            os.fchmod(saved.fileno(), 0o600)
            json.dump(cookies, saved, allow_nan=False)
            saved.flush()
            os.fsync(saved.fileno())
        os.replace(temporary_path, cookies_file)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                # An unwritable directory should not hide the original error.
                pass


@contextmanager
def browser_session(state_dir=DEFAULT_STATE, *, headed=False, session_result=None):
    """Yield the browser and atomically preserve session cookies on clean exit.

    ``session_result`` is an optional dict updated on exit with ``session_saved``.
    A manually closed browser cannot supply its current cookies; in that case
    the previous snapshot is retained and ``session_saved`` is false.
    """
    state_dir = private_directory(Path(state_dir))
    profile = private_directory(state_dir / "browser")
    # Chromium writes session cookies and login state here; restrict new files.
    old_umask = os.umask(0o077)
    lock_path = state_dir / "browser.lock"
    import fcntl

    outcome = session_result if session_result is not None else {}
    outcome["session_saved"] = False
    try:
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GolfError(
                    "The golf browser is already running. Finish login or close it first."
                ) from None
            with sync_playwright() as p:
                executable = (
                    os.environ.get("GOLF_BROWSER")
                    or shutil.which("chromium")
                    or shutil.which("google-chrome")
                )
                context = p.chromium.launch_persistent_context(
                    str(profile),
                    executable_path=executable,
                    headless=not headed,
                    locale="de-AT",
                    timezone_id="Europe/Vienna",
                    viewport={"width": 1440, "height": 1000},
                )
                context.set_default_timeout(15000)
                # Chromium discards session cookies when reopened, even with a
                # persistent profile. Restore those separately; localStorage
                # and IndexedDB already persist in the dedicated profile.
                cookies_file = state_dir / "cookies.json"
                context_closed = False
                initialized = False
                operation_failed = False

                def mark_closed(*_):
                    nonlocal context_closed
                    context_closed = True

                context.on("close", mark_closed)
                try:
                    _restore_cookies(context, cookies_file)
                    initialized = True
                    yield context
                except BaseException:
                    operation_failed = True
                    raise
                finally:
                    cleanup_error = None
                    try:
                        if initialized and not context_closed:
                            _save_cookies(context, cookies_file)
                            outcome["session_saved"] = True
                    except BaseException as exc:
                        if isinstance(exc, (PlaywrightError, OSError, ValueError, TypeError)):
                            if not context_closed:
                                cleanup_error = GolfError(
                                    "The current golf session could not be saved. The previous snapshot was preserved."
                                )
                        else:
                            cleanup_error = exc
                    finally:
                        try:
                            context.close()
                        except BaseException as exc:
                            if cleanup_error is None:
                                if isinstance(exc, PlaywrightError):
                                    if not context_closed:
                                        cleanup_error = GolfError(
                                            "The golf browser could not be closed cleanly."
                                        )
                                else:
                                    cleanup_error = exc
                    if cleanup_error is not None and not operation_failed:
                        raise cleanup_error from None
    finally:
        # This must also run if opening or acquiring the lock fails.
        os.umask(old_umask)


def safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def logged_in(page) -> bool:
    # Positive evidence only; a vanished form may just mean a loading screen.
    return page.locator('a[href*="logout"], a[href*="logoff"], a[href*="abmelden"]').count() > 0


def login(state_dir=DEFAULT_STATE, *, timeout_seconds=900):
    persistence = {}
    result = {"status": "login_timeout", "authenticated": None}
    with browser_session(state_dir, headed=True, session_result=persistence) as context:
        page = context.pages[0] if context.pages else context.new_page()
        response = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        _check_response(response)
        print(
            "Golf Austria is open. Sign in directly in the browser; your password stays there.",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if page.is_closed():
                result = {"status": "browser_closed", "authenticated": None}
                break
            try:
                if logged_in(page):
                    page.wait_for_timeout(1500)
                    result = {"status": "authenticated", "authenticated": True}
                    break
                page.wait_for_timeout(1000)
            except PlaywrightError:
                if not context.pages:
                    result = {"status": "browser_closed", "authenticated": None}
                    break
                raise
    return {**result, **persistence}


def _check_response(response):
    if response is None:
        raise GolfError("Golf Austria's login page did not return an HTTP response.")
    if response.status >= 400:
        raise GolfError(
            f"Golf Austria's login page returned HTTP {response.status}; authentication could not be checked."
        )


def session_status(state_dir=DEFAULT_STATE):
    with browser_session(state_dir) as context:
        page = context.pages[0] if context.pages else context.new_page()
        response = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        _check_response(response)
        if logged_in(page):
            return {"status": "authenticated", "authenticated": True, "url": safe_url(page.url)}
        if page.locator('input[name="loginpassword"]').count():
            return {
                "status": "authentication_required",
                "authenticated": False,
                "url": safe_url(page.url),
            }
        return {
            "status": "unavailable",
            "authenticated": None,
            "url": safe_url(page.url),
            "message": "The page did not contain a recognized login form or authenticated account navigation.",
        }


if __name__ == "__main__":
    try:
        print(json.dumps(login(), indent=2))
    except GolfError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)
    except PlaywrightError:
        print(
            json.dumps(
                {
                    "error": "The golf browser operation failed. Check the browser and network, then retry."
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)
