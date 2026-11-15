#!/usr/bin/env python3
"""Audit allowed Git blobs before sharing; never open private working files.

This is a guard against common mistakes, not proof that a tree has no secrets.
It checks the full index by default, or a committed tree with ``--ref``. Reject
unknown paths and nonregular files before inspecting content. Diagnostics show
only file names, line numbers, and categories, never matching text or values.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

TOP_LEVEL = {
    "README.md",
    "AGENTS.md",
    "LICENSE",
    "SECURITY.md",
    ".gitignore",
    ".gitattributes",
    "pyproject.toml",
    "MANIFEST.in",
}
FIXED_PATHS = {
    ".github/workflows/tests.yml",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    "golf_austria/data/clubs.json",
    "examples/candidates.json",
    "scripts/check_public_tree.py",
}
MAX_BLOB_BYTES = 1_000_000
PLACEHOLDERS = {
    "secret",
    "private",
    "token",
    "password",
    "changeme",
    "test",
    "fixture",
    "example",
    "synthetic-secret",
    "rotated-synthetic-secret",
    "not-a-real-secret",
    "your-api-key",
    "your-password",
    "your-token",
    "<password>",
    "<token>",
    "{password}",
    "{token}",
}
PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "provider_token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b"
        ),
    ),
    ("credential_url", re.compile(r"https?://[^\s/\"'<>:]+:[^\s/@\"'<>]+@", re.I)),
    (
        "authorization_value",
        re.compile(
            r"\b(?:authorization|proxy-authorization)[\"']?\s*[:=]\s*[\"'](?:Bearer|Basic)\s+[A-Za-z0-9+/=_-]{12,}",
            re.I,
        ),
    ),
    ("personal_home_path", re.compile(r"/(?:home|Users)/[^/\s\"'<>]+/")),
)
SECRET_ASSIGNMENT = re.compile(
    r"(?<![\w-])[\"']?(?:password|passwd|access_token|refresh_token|api_key|client_secret|"
    r"session_id|sessionid|basket_id|basketid|order_id|account_id|customer_id|book_as)[\"']?"
    r"\s*[:=]\s*[\"']([^\"'\n]+)[\"']",
    re.I,
)
SECRET_QUERY = re.compile(
    r"(?:[?&]|&amp;)(?:hash|token|access_token|refresh_token|sessionId|basketId|orderId|bookAs)="
    r"([^&#\s\"'<>]+)",
    re.I,
)
EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")


@dataclass(frozen=True)
class Entry:
    path: str
    mode: str
    oid: str
    stage: str = "0"


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    line: int | None = None

    def display(self):
        # repr prevents terminal control characters in malicious file names.
        location = repr(self.path) + (f":{self.line}" if self.line else "")
        return f"{location}: {self.category}"


def allowed_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or ".." in parts or "\\" in path:
        return False
    if path in TOP_LEVEL or path in FIXED_PATHS:
        return True
    if len(parts) == 2 and parts[0] == "golf_austria":
        return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", parts[1]) is not None
    if len(parts) == 2 and parts[0] == "tests":
        return re.fullmatch(r"test_[A-Za-z0-9_]+\.py", parts[1]) is not None
    if len(parts) == 2 and parts[0] == "docs":
        return re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.md", parts[1]) is not None
    return False


def _git(repo: Path, *arguments: str) -> bytes:
    completed = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True)
    if completed.returncode:
        # stderr may contain remote credentials or a user-provided ref; don't echo it.
        raise RuntimeError("Git could not read the requested index or tree.")
    return completed.stdout


def entries(repo: Path, ref: str | None = None) -> list[Entry]:
    if ref is None:
        records = _git(repo, "ls-files", "--stage", "-z")
    else:
        tree = (
            _git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{tree}")
            .decode("ascii")
            .strip()
        )
        records = _git(repo, "ls-tree", "-r", "-z", "--full-tree", tree)
    result = []
    for record in records.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        fields = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", "surrogateescape")
        if ref is None:
            mode, oid, stage = fields
        else:
            mode, kind, oid = fields
            stage = "0" if kind == "blob" else "unsupported"
        result.append(Entry(path, mode, oid, stage))
    return result


def _serialized_browser_state(value) -> bool:
    if isinstance(value, dict):
        if {"name", "value", "domain", "path"} <= value.keys():
            return True
        if isinstance(value.get("cookies"), list) or isinstance(value.get("origins"), list):
            return True
        return any(_serialized_browser_state(child) for child in value.values())
    if isinstance(value, list):
        return any(_serialized_browser_state(child) for child in value)
    return False


def inspect_content(path: str, raw: bytes) -> list[Finding]:
    if len(raw) > MAX_BLOB_BYTES:
        return [Finding(path, "oversized_public_file")]
    if b"\0" in raw:
        return [Finding(path, "binary_public_file")]
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return [Finding(path, "non_utf8_public_file")]
    findings = []
    for category, pattern in PATTERNS:
        for match in pattern.finditer(text):
            findings.append(Finding(path, category, text.count("\n", 0, match.start()) + 1))
    for category, pattern in (
        ("credential_literal", SECRET_ASSIGNMENT),
        ("sensitive_query_value", SECRET_QUERY),
    ):
        for match in pattern.finditer(text):
            value = match.group(1)
            if value.casefold() not in PLACEHOLDERS:
                findings.append(Finding(path, category, text.count("\n", 0, match.start()) + 1))
    for match in EMAIL.finditer(text):
        domain = match.group(1).lower()
        if domain not in {
            "example.com",
            "example.org",
            "example.net",
            "example.invalid",
            "users.noreply.github.com",
            "noreply.github.com",
        }:
            findings.append(
                Finding(
                    path, "email_address_review_required", text.count("\n", 0, match.start()) + 1
                )
            )
    if path.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            findings.append(Finding(path, "invalid_json"))
        else:
            if _serialized_browser_state(data):
                findings.append(Finding(path, "serialized_browser_state"))
    return sorted(set(findings), key=lambda item: (item.path, item.line or 0, item.category))


def audit(repo: Path, ref: str | None = None) -> tuple[int, list[Finding]]:
    records = entries(repo, ref)
    findings = []
    approved = []
    # Check the complete manifest before asking Git to read any blob contents.
    for entry in records:
        if not allowed_path(entry.path):
            findings.append(Finding(entry.path, "path_not_on_public_allowlist"))
        elif entry.mode not in {"100644", "100755"} or entry.stage != "0":
            findings.append(Finding(entry.path, "nonregular_or_unmerged_entry"))
        else:
            approved.append(entry)
    for entry in approved:
        size = int(_git(repo, "cat-file", "-s", entry.oid).strip())
        if size > MAX_BLOB_BYTES:
            findings.append(Finding(entry.path, "oversized_public_file"))
            continue
        findings.extend(inspect_content(entry.path, _git(repo, "cat-file", "blob", entry.oid)))
    if not records:
        findings.append(Finding("<index-or-tree>", "no_public_files_to_audit"))
    return len(approved), findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--staged", action="store_true", help="Audit every file in the Git index (default)"
    )
    source.add_argument("--ref", help="Audit a committed Git tree, e.g. HEAD")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        count, findings = audit(args.repo, args.ref)
    except (RuntimeError, OSError, ValueError):
        print("Public-tree audit could not read the requested Git tree.", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(finding.display(), file=sys.stderr)
        print(
            f"Public-tree audit failed: {len(findings)} finding(s). Matching values are never printed.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Public-tree audit passed for {count} allowed files. Review content before publishing; detection is not exhaustive."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
