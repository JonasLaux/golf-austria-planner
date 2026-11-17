# Golf Austria endpoint map

This records the observed upstream requests behind the unofficial CLI. Golf
Austria serves the data; this repository makes read-only requests and parses
HTML into anonymous JSON. It does not host a server or rely on a documented
public API contract.

The implemented interface is `golf times --direct` plus the repository's agent
skill. Direct reads are verified for Leopoldsdorf (`318`); scorecards and booking
mutations are not implemented. See [CLI and agent skill surface](#cli-and-agent-skill-surface)
for the current contract. The observations below are dated snapshots, not live
availability or a promise of national coverage.

## Verified public HTTP observations

Checked 2026-11-17 with bounded unauthenticated GET requests. No login form was
submitted, no cookies were used, and no booking or checkout URL was opened.

| Surface | Observation | What it proves |
| --- | --- | --- |
| `GET /mygolf/login` | `200 text/html`, staying on `www.golf.at/mygolf/login`. The page contains a `POST` form to the same path with `loginusername`, `loginpassword`, and hidden `a` and `wantedurl` fields. | The browser login is a normal site form. A JWT or bearer-token flow is not visible from the public login page. |
| `GET /suche/golfclubsuche?Clubname=Leopoldsdorf&lstSites=0&a=dosearch` | `200 text/html`, staying on `www.golf.at/suche/golfclubsuche`. The response contains the club-search form, one result card, and links for the club detail and `/startzeiten` page. | The current GET search works for a bounded query, but the page form itself advertises `POST /suche/golfclubsuche`; this is an observed website behavior, not a documented API contract. |
| `GET /suche/golfclubsuche?Clubname=&lstSites=0&a=dosearch` | `200 text/html` with 26 result cards and 24 published `/startzeiten` links in this response. The result includes a self-link with `a=dosearch`, `lstSites=0`, and `noscroll=1`. | `lstSites=0` means Austria (the select values 1–9 are the federal states). The count is only the observed response size; it does not prove that all Austrian clubs are returned or that `noscroll` is pagination. |
| Same empty Austria search, static result inspection | The page says “Es wurden mehr als 25 Clubs gefunden! Verfeinern Sie Ihre Suche!” and has no visible numeric page, next/previous, load-more, or letter-navigation control. Some cards also expose a `/teetimes/fwd.asp?clubnr=<id>` booking-entry link; it was not followed. | The result is visibly capped or intentionally asks for a narrower search. No club-list JSON or pagination endpoint emerged from this response. |
| `GET /suche/golfclubsuche` | `200 text/html`, staying on the directory page. The form advertises `POST /suche/golfclubsuche`, exposes a `Clubname` input, a `lstSites` select with 10 options, and several boolean/date filters. The page without the search trigger returned no club cards. | The directory has server-side search/filter controls, but this probe does not establish an all-clubs feed or pagination contract. |
| `GET /tt/startzeiten.asp?clubnr=318` | `200 text/html`, staying on `www.golf.at/tt/startzeiten.asp`. The unauthenticated response contains the `Userdaten nicht vorhanden` marker and no embedded `teetimes.golf.at` frame. | The entry point is an authenticated handoff. It does not expose a usable unauthenticated JSON tee-time endpoint. |

The public search response exposes HTML cards, not JSON. The result card shape
currently includes a numeric club ID, slug, name, address, detail URL, and an
optional `/startzeiten` URL. Coordinates and a provider issuer ID are not part
of this public response. The empty Austria search returned only one visible
self-navigation link; its `noscroll=1` parameter and script mentions of
pagination/offset are not enough to infer a paging endpoint. A result set may be
capped or loaded by another interaction, so an all-club enumerator still needs
verification.

## Evidence already in the repository

- [`clubs.py`](../golf_austria/clubs.py) builds the search GET with
  `Clubname`, `lstSites=0`, and `a=dosearch`, accepts only a final
  `www.golf.at` host, and parses the HTML card classes. It intentionally treats
  this as a website search, not a stable API.
- [`portal.py`](../golf_austria/portal.py) first opens
  `/tt/startzeiten.asp?clubnr=<id>`, then waits for a frame at
  `https://teetimes.golf.at/mp1/search`. It reads `issuerId`, preserves that
  value, and navigates the frame to the same path with `date=DD.MM.YYYY`,
  `hour=6`, and `minute=0`.
- [`calendar.py`](../golf_austria/calendar.py) parses the provider's HTML. It
  verifies the displayed date, configured issuer, and optional course name, then
  extracts products and anonymous availability from HTML links. It does not
  consume a JSON response.
- The calendar parser recognizes booking actions at
  `/mp1/checkout/book` and
  `/widgets-web/booking/submitSelectProductForm` only to retain opaque public
  slot identifiers. Those URLs can initialize a basket or submit a booking and
  must not be fetched during discovery or API mapping.
- [`browser.py`](../golf_austria/browser.py) persists a dedicated browser
  profile plus a cookie snapshot. It does not parse or issue a JWT. The actual
  login POST and session-refresh behavior are outside the tested direct-read
  path; cookie expiry and renewal remain unverified.

## Verified authenticated request mapping

Checked 2026-11-17 with a fresh dedicated browser session and one read-only
calendar flow for club `318`. This note retains request shapes, nonsensitive
filter values, and anonymous validation summaries. It contains no handoff
values, credentials, player names, or raw pages. The existing login helper
saved private session state outside the repository, as usual.

| Step | Request shape | Observation |
| --- | --- | --- |
| Published club routes | `GET /golfclubs/golf-country-club-brunn/312/startzeiten` and `GET /golfclubs/c-c-golfclub-am-wienerberg/902/startzeiten` | Both returned `200 text/html` and embedded the same `/tt/startzeiten.asp?clubnr=<id>` wrapper. No alternate provider calendar was observed. |
| Club wrapper | `GET www.golf.at/tt/startzeiten.asp` with a `clubnr` query key | The Golf Austria page starts the authenticated handoff. |
| Identity handoff | `GET teetimes.golf.at/user/landing/golfAt` with query keys `clubnr`, `clubnummern`, `email`, `firstname`, `hash`, `lastname`, `oegvid`, `showname`, `target` | The provider returned `303`. The `hash` is a site-generated handoff field; its construction and signing algorithm are unknown. |
| Calendar load | `GET teetimes.golf.at/mp1/search` with keys `issuerId`, `restricted`, `searchType` | `200 text/html`. |
| Date change | `GET teetimes.golf.at/mp1/search` with keys `date`, `hour`, `issuerId`, `minute`, `restricted`, `searchType`; the browser used `restricted=true` and `searchType=teeTime` | `200 text/html`; the displayed date matched the requested date. |

The recorded Golf Austria/provider document/XHR/fetch requests carried a
cookie header and none carried an `Authorization` header. A cookie is part of the browser
flow, but it is not required for the tested read endpoint once the issuer is
known: after the browser was closed, a standalone Playwright HTTP context with
cookies and `origins=[]` reproduced the parser result, and a completely fresh
HTTP context with no cookies reproduced the same normalized result for the same
issuer and date. This does not generalize to the login/handoff, club discovery,
account-dependent visibility, booking, or checkout pricing.

The browser response for club `318` exposed 48 calendar rows and 40 anonymous
candidates for 18 November; the same route exposed 46 candidates for 19
November. A standalone request with only `issuerId`, `date`, `hour`, and
`minute` returned `200 text/html` but lacked the date control and rows, so
the parser correctly rejected it. Preserving `searchType=teeTime` and the
other browser query parameters produced valid HTML. Removing `restricted` alone
still produced 40 candidates; removing `searchType` made the response
unparseable despite HTTP 200.

The cookies and no-cookie contexts produced the same normalized parser result
for 18 November, including date, calendar-slot count, available-player counts,
prices, products, course, notices, and opaque slot references. This is parity
for one tested issuer/date, not proof that every club or date is public.

The legacy wrapper returned `access_denied` for clubs `902` and `312` in
bounded checks. Those denials only describe that wrapper route; they do not prove
the account cannot access the club or that the provider endpoint is absent. The
published `/startzeiten` pages did not show a different provider route or a
`/teetimes/fwd.asp` link in this check. No booking or checkout URL was
requested, and no extra JSON club/availability request appeared.

The successful club `318` flow and the direct-read proof are:

```
login in dedicated browser
  -> /tt/startzeiten.asp?clubnr=...
  -> /user/landing/golfAt?... -> 303
  -> /mp1/search -> verified club-to-issuer mapping

known issuer + date
  -> direct GET /mp1/search?issuerId=...&searchType=teeTime&date=...&hour=6&minute=0
  -> HTML calendar parser
```

The direct endpoint is `https://teetimes.golf.at/mp1/search`. The observed date
format is `DD.MM.YYYY`. The browser also sends `restricted=true`; omitting it
worked in this bounded check, but broader semantics are unverified. The numeric
Golf Austria `clubnr` and the provider's `issuerId` are different identifiers.

## CLI and agent skill surface

### Thin direct-read path

The verified direct read is now exposed as a thin CLI path:

```bash
.venv/bin/golf times --direct --club 318 --date YYYY-MM-DD
```

`--hour` and `--minute` optionally set the provider's query window. Direct mode
uses the configured `calendar_issuer_id` and `calendar_course_names`, sends the
observed `searchType=teeTime` and `restricted=true` parameters, refuses redirects,
and parses the returned HTML through the existing validation boundary. It does
not launch Chromium, read cookies, or use the saved state directory. A missing
verified mapping is reported as `calendar_requires_mapping` rather than guessed.

The result keeps the upstream request visible:

```json
{
  "transport": "direct_http",
  "authentication": "public",
  "query": {
    "date": "DD.MM.YYYY",
    "hour": 6,
    "minute": 0,
    "searchType": "teeTime",
    "restricted": "true"
  },
  "slots": [],
  "candidates": []
}
```

The example is shape-only. `slots` contains every observed row, including full
and partly booked times, with anonymous `available_players`, `booked_players`,
`unknown_players`, and `occupancy_known` fields. `candidates` retains the
existing free-seat offerings and booking identifiers without exposing names or
checkout URLs. The CLI does not apply a fixed buffer, quiet-time rule, weather
filter, or calendar conflict rule; an agent can interpret the slot data against
the user's request and other services.

The companion skill is [`skills/golf-austria/SKILL.md`](../skills/golf-austria/SKILL.md).
It tells an agent when to use direct reads, how to interpret `slots` versus
`candidates`, and when to fall back to the dedicated browser. It does not grant
booking authority. Scorecard history remains unmapped beyond the observed
authenticated route.

### Tee-time capabilities first

A further read-only check on 2026-11-17 validated the course and date for club
`318`, 18 November, before inspecting anonymous row structure. It found 48
unique start times at continuous 10-minute intervals: 30 entirely empty rows,
10 partly booked rows, and 8 fully booked rows. Every player position in this
response had a recognized state:

- Free places were explicit `a.player-box.none` booking links. Those links were
  counted but never followed.
- Occupied places were explicit `div.player-box.booked` elements. Their names
  and other text are unnecessary and must not enter the output.
- Fully occupied rows also had the `fullybooked` class. Occupancy was counted
  from individual marked places, without guessing a fixed capacity or using
  colors.

The parser now returns free-seat offerings as `candidates` and retains every
observed row in `slots`, including fully booked rows. Each slot carries the
anonymous occupied/free counts, course/date identity, and observation timestamp
through the surrounding response. Missing, blocked, or unfamiliar cells remain
unknown; an unavailable place is not automatically an occupied place.

An agent can use this data to check for booked groups within a requested buffer
before and after a candidate on the same verified course. That check needs the
surrounding sheet, including fully booked times, and must return
unknown when the requested window extends beyond verified coverage or has
unexplained gaps. Do not assume a 10-minute interval for other clubs. Whether
the candidate itself must be entirely empty is a separate user preference.
The result describes a snapshot, not a reservation of the surrounding times.

Weather and appointment decisions remain with the calling agent and its other
services. The thin CLI and companion skill expose the raw observations; they
do not implement those request-specific rules or reserve surrounding times.

### Personal results as a later capability

Authenticated navigation exposed `GET /mygolf/stammblattabfrage`; opening it
returned `200` with a table and no login form. It also linked to
`/mygolf/stammblattabfrage/gesamt`, which was not followed. This establishes an
accessible handicap/scoring-history entry, not a verified scorecard API.
Individual scorecards, hole-by-hole detail, ordering of recent rounds, and an
export format still need investigation. No personal scores or account history
were copied into this note, and no score entry was created or changed.

### Implementation boundary

1. Keep public club discovery as a small HTML client. Prefer the existing GET
   while it works, but be ready to reproduce the form's POST if the site stops
   accepting query URLs.
2. Direct reads are exposed for club `318` with its verified issuer and nonempty
   course-name mapping. Preserve `issuerId`, date, course, and HTML validation,
   and keep the browser handoff as the fallback for issuer discovery and routes
   that require account context.
3. Do not assume every club uses the same provider or issuer. Resolve and cache
   that mapping only after each club's response is verified.

## Unverified behavior

- Whether cookies are required for other clubs, date windows, issuer discovery,
  or account-specific availability; the no-cookie parity only covers one tested
  read route.
- Login POST semantics, cookie expiry/renewal, and any short-lived handoff
  constraints remain untested. The observed browser flow has no
  `Authorization` header, but that is not a universal JWT claim.
- Whether the `hash` handoff field is stable, signed, short-lived, or bound to
  the browser session. Its value and algorithm are intentionally unknown.
- Whether all clubs share `teetimes.golf.at`, the same parameters, and the same
  product/availability schema.
- Pagination, date-range limits, rate limits, bot checks, and provider terms.
- Whether listed prices remain valid at checkout. This project remains read-only
  and hands the user back to the browser for a manual booking.

## Current outcome

This document records endpoint mapping and the first thin CLI/skill surface. It
does not add an HTTP server, booking automation, or request-specific planning
rules. Broader issuer discovery, session lifecycle behavior, and provider
coverage remain open.

## Sources

- [Golf Austria login](https://www.golf.at/mygolf/login)
- [Golf Austria club directory](https://www.golf.at/suche/golfclubsuche)
- [Golf Austria app feature overview](https://www.golf.at/golf-austria-app)
- [Nexxchange portal booking FAQ](https://nexxchange.atlassian.net/wiki/spaces/POR/pages/2913075203/H%C3%A4ufig%2Bgestellte%2BFragen%2BFAQ%2Bzum%2BNexxchange%2BMarktplatz%2BPortal)
