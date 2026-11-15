# Working on Golf Austria Planner

This is an unofficial Python CLI that reads tee-time calendars and ranks rounds
using hourly weather. Keep it useful from a terminal and composable through JSON.
There is no Hermes dependency and no automatic reservation submission.

## Layout

- `golf_austria/browser.py`: dedicated Chromium profile, authentication and private cookies.
- `golf_austria/portal.py`: authenticated navigation, club/date verification and browser handoff.
- `golf_austria/calendar.py`: pure HTML parsing into anonymous tee-time candidates.
- `golf_austria/weather.py`: forecast retrieval, time windows and deterministic ranking.
- `golf_austria/clubs.py`: public club directory search.
- `golf_austria/cli.py`: command orchestration, JSON output and exit codes.
- `golf_austria/data/clubs.json`: public example club metadata, with sources.
- `tests/`: synthetic data and mocked services; no account or live-network dependency.

## Development

Use Python 3.12 or newer on Linux. From a checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Run the relevant tests while editing and the complete suite before finishing.
Changes to packaging must also pass a built-wheel install and CLI smoke check
from outside the source directory. CI exercises Python 3.12, 3.13 and 3.14.
Keep dependency additions justified; parsers and ranking currently use the
standard library. The only runtime dependency is Playwright.

## Data and privacy

- Do not read or publish real cookies, passwords, authentication handoff URLs,
  browser profiles, account exports, screenshots, or personal recommendations
  to investigate a test failure. Reproduce it with synthetic data.
- Runtime state belongs outside the repository. The default is
  `$XDG_STATE_HOME/golf-austria`, falling back to `~/.local/state/golf-austria`.
  `GOLF_STATE_DIR` and `--state-dir` can override it. `.private/` is a legacy,
  ignored local directory, never a fixture source for shared tests.
- Session cookies are credentials. Keep directories at `0700`, snapshots at
  `0600`, atomic writes, and sanitized errors. Never print cookie values or
  raw Playwright errors. Restore short-lived cookies across browser launches.
- Tee sheets contain other golfers' names and handicaps. Return anonymous
  availability counts, not player identities. Retain only the public slot and
  product identifiers needed to resolve a selected time.
- Keep examples fictional and explicitly labelled. Club addresses and course
  coordinates may be public configuration; a user's home or account is not.
- Before a commit or publication, inspect the exact staged files and run
  `python scripts/check_public_tree.py --staged`. After committing, use
  `python scripts/check_public_tree.py --ref HEAD`. These checks supplement
  review; they do not establish that arbitrary files are safe.
- Build distributions from tracked source. Never upload a recursive workspace
  ZIP or include ignored runtime folders in an archive.

## Behavior to preserve

- Login and booking handoff use a dedicated profile, never the everyday browser.
- Availability lookup is read-only. Some checkout URLs initialize an order
  basket through GET: do not visit them during scraping, prefetching, tests,
  link validation, or documentation generation.
- Validate the selected club, redirects, and the displayed calendar date. A
  provider fallback must not become a plausible answer for a different course.
- Authentication failures, denied access, unknown markup, no availability, and
  missing weather are different outcomes. Preserve useful partial results.
- Parse the offered product's hole count, active EUR price and explicit free
  player links. Do not infer capacity from color or choose a crossed-out price.
- Use aware timestamps. Rank weather over the entire assumed round, respecting
  the forecast's interval semantics, freshness, daylight and sunset margin.
- Missing values stay unknown. The score is a preference heuristic, not a
  forecast probability. Default durations are 120 minutes for 9 holes and
  270 minutes for 18 holes; disclose explicit overrides.
- Course notices are advisories unless they establish a closure. A closed
  driving range does not mean the course is closed; a frost rule does not prove
  frost is present.
- `eligible` describes recommendation suitability. `bookable` stays false while
  automatic checkout is unimplemented. Never report a reservation as confirmed
  without evidence from the provider.

## Documentation and changes

Explain the concrete behavior, validation and limitations of a change. Do not
claim support from a fixture alone or advertise untested providers. Update the
README when commands, state paths, defaults or privacy behavior change. Use
official provider documentation and observed behavior for integrations; do not
invent APIs. Add synthetic regressions for changed authentication, parsing,
price, date, daylight and partial-failure behavior.
