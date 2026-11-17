---
name: golf-austria
description: Query Golf Austria tee-time and club data through this repository's JSON CLI for agent workflows; use for live read-only golf availability, never for autonomous booking.
---

# Golf Austria

Use this skill when an agent needs current Golf Austria club or tee-time data.
The CLI is a thin wrapper around the observed upstream pages. The agent owns
the user's natural-language constraints and combines this data with weather,
calendar, travel, or other services.

Resolve the executable as `golf` from `PATH` when installed, otherwise use the
checkout's `.venv/bin/golf` while working from the repository directory. Do not
assume a repository-relative path exists in another agent's environment.
If neither exists, use the checkout's README to install the CLI first; this
skill does not bundle an executable or assume a published package.

## Tee times

For the verified direct provider mapping, run:

```bash
golf times --direct --club 318 --date YYYY-MM-DD
```

Use `--hour` and `--minute` when the upstream query needs a different window;
they default to `6` and `0`. Direct mode requires both a configured provider
issuer and a nonempty verified course-name list. It does not read browser state.
The command returns JSON with `transport: "direct_http"`,
`authentication: "public"`, the sanitized upstream `query`, every observed
`slots` row, and the existing free-seat `candidates`.

`slots` is the tee sheet. It includes empty, partly booked, and fully booked
times with anonymous `available_players`, `booked_players`,
`unknown_players`, and `occupancy_known` values. `candidates` is narrower: it
contains offerings with explicit free-player links and product details. Use
`slots` for occupancy and neighbouring groups; use `candidates` for hole counts,
prices, and the available places for a particular offering. Match them by start
time within the same verified club/course. Multiple product groups can make
`slots.available_players` null and `occupancy_known` false; do not add their
availability counts together. Apply request-specific constraints:

- a time window (`10:00` to `11:00`);
- whether joining an existing group is allowed;
- requested gaps before or after a chosen start without booked places;
- the requested number of available places, holes, or price.

Do not hardcode any of those preferences. Joining an existing group includes
empty and partly booked rows when the user allows it. Use the aware start times
and `verified_at` timestamp. If coverage or occupancy is unknown, report the
condition as unknown instead of treating it as empty or available. Missing rows
or data outside the returned window do not prove a clear buffer. Recheck before
presenting a slot for booking; the result is a snapshot, not a reservation.

`clubs QUERY` searches Golf Austria's public directory, but the broad search is
capped and does not establish a complete national list. Use a configured club
ID and verified provider mapping; do not invent an issuer or course name. A
missing mapping returns `calendar_requires_mapping`.

## Browser actions

Use `login` when the authenticated path is required and the user can complete
the sign-in in the dedicated visible browser. It keeps credentials in the
private state directory. Use `status` to check that state. Use `open` only as a
user-facing booking handoff after the user has chosen a fresh slot; it never
confirms a booking.
Never request, print, store, or paste passwords, cookies, handoff hashes,
player names, or checkout URLs into agent output.

Do not follow checkout links or prefetch them. Some provider checkout GETs can
initialize an order basket. Available places do not authorize or confirm a
booking. Direct tee-time output has no `bookable` field; ranked planner output
keeps it false.

## Limits

The direct path is verified for the bundled Leopoldsdorf mapping (`318`). Other
clubs, issuer discovery, account-specific visibility, session renewal, and
checkout prices need separate verification. Scorecard history is not exposed by
this skill yet; only an authenticated history page has been observed.

Exit codes are `0` for completed output, `1` for validation or command
errors, and `2` for partial, unavailable, or unmapped results. Treat exit `2` as
a reason to inspect the JSON status and messages; it is not proof that no tee
time exists. Invalid command syntax also exits `2` with usage on stderr and no
JSON result. Runtime errors are JSON on stderr; successful query output is JSON
on stdout.
