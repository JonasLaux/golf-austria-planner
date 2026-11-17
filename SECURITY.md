# Security and sensitive data

Direct tee-time reads use a mapped public calendar endpoint without an account,
cookies, or browser state. Direct mode rejects all redirects. Lookup commands
never follow checkout links; even a checkout GET can initialize an order basket.

The optional browser commands use a real Golf Austria account in a dedicated
profile. The cookie snapshot is stored locally in plaintext with owner-only
permissions. Anyone who can read it or the profile may gain account access.
It is not an encrypted credential vault. Tee-time output contains anonymous
occupancy counts and public product identifiers, not other golfers' identities.

The default runtime directory is outside this repository:
`$XDG_STATE_HOME/golf-austria` or `~/.local/state/golf-austria`. Never attach it,
legacy `.private/` directories, raw HTML dumps, browser traces, screenshots of
tee sheets, or actual recommendation exports to public issues or pull requests.
Use a minimal synthetic reproduction instead.

Before publishing a commit, inspect the exact staged diff and run
`python scripts/check_public_tree.py --staged`; after committing, run it with
`--ref HEAD`. Build distributions from tracked source, not a recursive archive
of the workspace. The repository skill and endpoint notes are public source;
session state and personal exports are not.

To report a vulnerability, use this repository's **Security → Report a
vulnerability** feature when available. Do not include active credentials or
other golfers' personal data. If private reporting is unavailable, open a
minimal issue asking for a private reporting channel without exploit details
or sensitive attachments.

If a login cookie is exposed, remove the public material and revoke or sign out
the affected session through the provider. Deleting a Git file in a later
commit does not remove it from history. A scan with no findings is useful
evidence, not a guarantee that no secret exists.
