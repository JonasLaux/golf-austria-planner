"""Public-tree checks use temporary repositories and constructed fake secrets."""

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_public_tree.py"
SPEC = importlib.util.spec_from_file_location("public_tree_checker", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def fake_token():
    return "gh" + "p_" + "A1" * 18


def git(repo, *arguments):
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, check=True)
    return result.stdout


class PublicTreeTests(unittest.TestCase):
    def test_only_the_named_skill_document_is_allowed(self):
        self.assertTrue(checker.allowed_path("skills/golf-austria/SKILL.md"))
        self.assertFalse(checker.allowed_path("skills/golf-austria/cookies.json"))
        self.assertFalse(checker.allowed_path("skills/other/SKILL.md"))

    def test_private_and_unknown_paths_are_rejected_without_reading_their_blobs(self):
        entries = [
            checker.Entry(".private/cookies.json", "100644", "private-oid"),
            checker.Entry("reports/account.json", "100644", "report-oid"),
            checker.Entry("backup.zip", "100644", "archive-oid"),
        ]
        with (
            patch.object(checker, "entries", return_value=entries),
            patch.object(checker, "_git") as access,
        ):
            count, findings = checker.audit(Path("/unused"))
        self.assertEqual(count, 0)
        self.assertEqual(len(findings), 3)
        access.assert_not_called()

    def test_allowed_name_cannot_smuggle_a_symlink_or_submodule(self):
        entries = [
            checker.Entry("README.md", "120000", "symlink-oid"),
            checker.Entry("golf_austria/main.py", "160000", "submodule-oid"),
        ]
        with (
            patch.object(checker, "entries", return_value=entries),
            patch.object(checker, "_git") as access,
        ):
            count, findings = checker.audit(Path("/unused"))
        self.assertEqual(count, 0)
        self.assertTrue(all(item.category == "nonregular_or_unmerged_entry" for item in findings))
        access.assert_not_called()

    def test_findings_never_include_matching_values(self):
        token = fake_token()
        findings = checker.inspect_content("README.md", ("Example\n" + token + "\n").encode())
        self.assertTrue(
            any(item.category == "provider_token" and item.line == 2 for item in findings)
        )
        output = io.StringIO()
        with patch.object(checker, "audit", return_value=(1, findings)), redirect_stderr(output):
            self.assertEqual(checker.main([]), 1)
        self.assertNotIn(token, output.getvalue())

    def test_test_source_is_scanned_too(self):
        findings = checker.inspect_content(
            "tests/test_example.py", ("value = '" + fake_token() + "'\n").encode()
        )
        self.assertTrue(any(item.category == "provider_token" for item in findings))

    def test_browser_snapshot_is_rejected_even_under_an_allowed_json_name(self):
        raw = json.dumps(
            [
                {
                    "name": "fixture",
                    "value": "synthetic-secret",
                    "domain": "example.invalid",
                    "path": "/",
                }
            ]
        ).encode()
        findings = checker.inspect_content("examples/candidates.json", raw)
        self.assertTrue(any(item.category == "serialized_browser_state" for item in findings))

    def test_public_club_ids_and_explicit_fixture_placeholders_are_not_secrets(self):
        raw = b'{"calendar_issuer_id":"5d43e55ed1684750513f057f","club_id":"318","source_url":"https://example.invalid/?hash=SECRET&bookAs=PRIVATE"}'
        self.assertEqual(checker.inspect_content("golf_austria/data/clubs.json", raw), [])

    def test_personal_home_path_and_sensitive_query_are_detected_without_values(self):
        home = "/" + "home" + "/" + "fixture-person" + "/notes"
        query = "https://example.invalid/?" + "hash=" + "made-up-auth-material"
        findings = checker.inspect_content("docs/example.md", (home + "\n" + query).encode())
        self.assertEqual(
            {item.category for item in findings}, {"personal_home_path", "sensitive_query_value"}
        )
        self.assertTrue(
            all(home not in item.display() and query not in item.display() for item in findings)
        )

    def test_index_and_committed_tree_audit_exact_blobs_not_working_files(self):
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "--quiet")
            (repo / "README.md").write_text("Public documentation.\n")
            git(repo, "add", "README.md")
            (repo / "README.md").write_text(fake_token())
            # Untracked ignored files must not be opened by the checker.
            (repo / ".private").mkdir()
            (repo / ".private" / "cookies.json").write_text("private fixture not to be read")
            count, findings = checker.audit(repo)
            self.assertEqual((count, findings), (1, []))
            git(
                repo,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "Public fixture",
            )
            self.assertEqual(checker.audit(repo, "HEAD"), (1, []))
            git(repo, "add", "README.md")
            self.assertTrue(
                any(item.category == "provider_token" for item in checker.audit(repo)[1])
            )


if __name__ == "__main__":
    unittest.main()
