import unittest
from unittest.mock import patch

from golf_austria.clubs import ClubSearchError, parse_clubs, search_clubs


class ClubParsingTests(unittest.TestCase):
    def test_parses_result_cards_and_ignores_navigation_and_external_links(self):
        html = """
        <a href="/golfclubs/navigation/1">Do not include</a>
        <div class="search-result-club">
          <div class="search-result-club__content">
            <h3 class="search-result-club__headline">Golf &amp; Friends</h3>
            <p class="search-result-club__address">Club road 2<br>A-1000 Town</p>
            <div><a href="https://evil.example/golfclubs/fake/666">Ignore</a>
              <a href="/golfclubs/golf-friends/123">Details</a>
              <a href="/golfclubs/golf-friends/123/startzeiten">Book</a>
            </div>
          </div>
        </div>
        """
        self.assertEqual(
            parse_clubs(html),
            [
                {
                    "name": "Golf & Friends",
                    "address": "Club road 2 A-1000 Town",
                    "id": "123",
                    "slug": "golf-friends",
                    "url": "https://www.golf.at/golfclubs/golf-friends/123",
                    "booking_url": "https://www.golf.at/golfclubs/golf-friends/123/startzeiten",
                }
            ],
        )

    def test_missing_booking_link_stays_unknown(self):
        html = """<div class="search-result-club">
        <h3 class="search-result-club__headline">Test club</h3>
        <a href="/golfclubs/test/99">Details</a><a href="">No booking link</a>
        </div>"""
        self.assertIsNone(parse_clubs(html)[0]["booking_url"])

    def test_empty_search_is_distinct_from_unrecognized_page(self):
        self.assertEqual(parse_clubs('<form class="search-club"></form>'), [])
        with self.assertRaises(ClubSearchError):
            parse_clubs("<html>Service unavailable</html>")

    def test_empty_query_does_not_make_network_request(self):
        with patch("golf_austria.clubs.urlopen") as request:
            with self.assertRaises(ValueError):
                search_clubs(" ")
            request.assert_not_called()

    def test_duplicate_cards_are_deduplicated_by_id(self):
        card = """<div class="search-result-club">
        <h3 class="search-result-club__headline">Test club</h3>
        <a href="/golfclubs/test/99">Details</a></div>"""
        self.assertEqual(len(parse_clubs(card * 2)), 1)


if __name__ == "__main__":
    unittest.main()
