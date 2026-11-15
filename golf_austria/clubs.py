"""Read Golf Austria's public club directory without an authenticated session."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

BASE_URL = "https://www.golf.at"
DIRECTORY_URL = BASE_URL + "/suche/golfclubsuche"
_CLUB_PATH = re.compile(r"^/golfclubs/([^/]+)/(\d+)(/startzeiten)?/?$")
_MAX_HTML_BYTES = 4_000_000


class ClubSearchError(RuntimeError):
    """The directory request failed or returned an unexpected document."""


class _ClubParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.clubs: list[dict] = []
        self._card: dict | None = None
        self._div_depth = 0
        self._text_field: str | None = None
        self._text_tag: str | None = None
        self.saw_search_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "form" and "search-club" in classes:
            self.saw_search_form = True
        if tag == "div" and "search-result-club" in classes:
            self._finish_card()
            self._card = {"name": "", "address": "", "url": None, "booking_url": None}
            self._div_depth = 1
            return
        if self._card is None:
            return
        if tag == "div":
            self._div_depth += 1
        if "search-result-club__headline" in classes:
            self._text_field, self._text_tag = "name", tag
        elif "search-result-club__address" in classes:
            self._text_field, self._text_tag = "address", tag
        elif tag == "br" and self._text_field:
            self._card[self._text_field] += " "
        if tag != "a":
            return
        href = (attributes.get("href") or "").strip()
        if not href:
            return
        url = urljoin(BASE_URL + "/", href)
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "www.golf.at":
            return
        match = _CLUB_PATH.fullmatch(parts.path)
        if match is None:
            return
        self._card["id"] = match.group(2)
        self._card["slug"] = match.group(1)
        key = "booking_url" if match.group(3) else "url"
        self._card[key] = url

    def handle_data(self, data: str) -> None:
        if self._card is not None and self._text_field:
            self._card[self._text_field] += data

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return
        if tag == self._text_tag:
            self._text_field = self._text_tag = None
        if tag == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self._finish_card()

    def _finish_card(self) -> None:
        if self._card is not None:
            card = self._card
            card["name"] = " ".join(card["name"].split())
            card["address"] = " ".join(card["address"].split())
            if card.get("id") and card["name"] and card["url"]:
                self.clubs.append(card)
        self._card = None
        self._div_depth = 0
        self._text_field = self._text_tag = None


def parse_clubs(html: str) -> list[dict]:
    """Parse actual result cards; absent booking links remain unknown (None)."""
    parser = _ClubParser()
    parser.feed(html)
    parser.close()
    parser._finish_card()
    if not parser.saw_search_form and not parser.clubs:
        raise ClubSearchError("Golf Austria returned an unrecognized club-search page.")
    return list({club["id"]: club for club in parser.clubs}.values())


def search_clubs(query: str) -> list[dict]:
    """Search by name, returning id, slug, name, address, url and booking_url.

    IDs are strings. This is the public site's search form, not a documented API.
    Results can be a subset of all matching clubs; use a specific name. A missing
    booking link means the directory did not publish one, not that booking is
    impossible. Coordinates are not present in the observed directory response.
    """
    query = query.strip()
    if not query:
        raise ValueError("Enter a club name to search for.")
    url = DIRECTORY_URL + "?" + urlencode({"Clubname": query, "lstSites": "0", "a": "dosearch"})
    request = Request(url, headers={"User-Agent": "GolfAustriaLocalPlanner/0.1"})
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(_MAX_HTML_BYTES + 1)
            if len(body) > _MAX_HTML_BYTES:
                raise ClubSearchError("Golf Austria's club-search response is unexpectedly large.")
            if urlsplit(response.url).netloc != "www.golf.at":
                raise ClubSearchError("Golf Austria redirected the search to an unexpected host.")
            charset = response.headers.get_content_charset() or "utf-8"
            html = body.decode(charset)
    except ClubSearchError:
        raise
    except (OSError, UnicodeError, LookupError) as exc:
        raise ClubSearchError("Could not read Golf Austria's public club directory.") from exc
    return parse_clubs(html)
