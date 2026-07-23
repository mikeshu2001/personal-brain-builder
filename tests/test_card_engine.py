from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "build-personal-brain" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from card_engine import (  # noqa: E402
    contains_secret_value_text,
    search_cards,
    validate_cards,
)


class CardEngineTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="card-engine-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.brain = Path(self.temp_dir.name) / "brain"
        self.brain.mkdir()
        for root in (
            "profile",
            "projects",
            "rules",
            "links",
            "journal",
            "inferred",
            "inbox",
            "examples",
            "system",
            "work",
        ):
            (self.brain / root).mkdir()
        setup = self.brain / "work" / "setup"
        setup.mkdir()
        source_root = (self.brain.parent / "source").resolve()
        roots_path = setup / "source-roots.json"
        roots_path.write_text(
            json.dumps({"root-one": str(source_root)}) + "\n",
            encoding="utf-8",
        )
        roots_path.chmod(0o600)
        host_history = (self.brain.parent / "host-history").resolve()
        agent_history = (self.brain.parent / "agent-history").resolve()
        state_path = setup / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "state": "READY",
                    "scope": [
                        {
                            "path": str(source_root),
                            "mode": "content",
                            "consent_scope": [str(source_root)],
                        }
                    ],
                    "consents": [],
                    "areas": {
                        "source": {
                            "paths": [str(source_root)],
                            "coverage": "content-reviewed",
                            "execution": "closed",
                        }
                    },
                    "session_history": {
                        "host": {
                            "path": str(host_history),
                            "coverage": "transcripts-reviewed",
                            "execution": "closed",
                        },
                        "agent": {
                            "path": str(agent_history),
                            "coverage": "distillates-reviewed",
                            "execution": "closed",
                        },
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state_path.chmod(0o600)

    def write_card(
        self,
        card_id: str,
        lines: Iterable[str],
        body: Optional[str] = None,
    ) -> Path:
        path = self.brain / (card_id + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "---\n{}\n---\n\n{}\n".format(
            "\n".join(lines),
            body or "# Synthetic card\n",
        )
        path.write_text(content, encoding="utf-8")
        return path

    def stated_lines(
        self,
        title: str,
        *,
        canonical_key: Optional[str] = None,
        extra: Iterable[str] = (),
    ) -> list:
        lines = [
            'title: "{}"'.format(title),
            "type: rule",
            "created: 2026-01-01",
            "origin: stated",
            "stated_in: session:owner-a",
            'summary: "Shared search needle"',
            "tags: [shared, needle]",
        ]
        if canonical_key:
            lines.append('canonical_key: "{}"'.format(canonical_key))
        lines.extend(extra)
        return lines

    def imported_lines(self, title: str) -> list:
        return [
            'title: "{}"'.format(title),
            "type: rule",
            "created: 2026-01-01",
            "origin: imported",
            "imported_at: 2026-01-02",
            'summary: "Shared search needle"',
            "tags: [shared, needle]",
            "sources:",
            '  - file: "root-one:path/to/source.md#Rule"',
        ]

    def inferred_lines(
        self,
        title: str,
        *,
        risk: Optional[str] = None,
        sessions: Iterable[str] = ("host:session-a", "host:session-b"),
    ) -> list:
        lines = [
            'title: "{}"'.format(title),
            "type: rule",
            "created: 2026-01-01",
            "origin: inferred",
            'summary: "Shared search needle"',
            "tags: [shared, needle]",
        ]
        if risk:
            lines.append("risk: {}".format(risk))
        lines.append("receipts:")
        for index, session in enumerate(sessions, start=1):
            lines.extend(
                [
                    '  - session: "{}"'.format(session),
                    "    date: 2026-01-{:02d}".format(index),
                    '    quote: "Independent evidence {}"'.format(index),
                ]
            )
        return lines

    def observation_lines(self, title: str) -> list:
        return [
            'title: "{}"'.format(title),
            "type: observation",
            "created: 2026-01-01",
            "origin: observation",
            'summary: "Shared search needle"',
            "tags: [shared, needle]",
        ]

    def assert_error_contains(self, report: dict, fragment: str) -> None:
        self.assertFalse(report["ok"], report)
        self.assertTrue(
            any(fragment in error for error in report["errors"]),
            "missing {!r} in {!r}".format(fragment, report["errors"]),
        )

    def test_valid_minimal_card_and_inline_scalars(self) -> None:
        lines = self.stated_lines(
            "Concise output",
            canonical_key="rules/concise-output",
            extra=("volatile: false",),
        )
        lines[6] = 'tags: ["shared, needle", \'owner\'\'s\']'
        self.write_card(
            "rules/concise-output",
            lines,
        )
        report = validate_cards(self.brain)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["cards_checked"], 1)
        self.assertEqual(report["canonical_cards"], 1)

    def test_common_contextual_secret_shapes_are_detected(self) -> None:
        samples = (
            "cookie=synthetic-cookie-value-12345",
            (
                "eyJabcdefghijk.eyJlmnopqrst."
                "abcdefghijklmnopqrstuvwxyz"
            ),
            (
                "recovery phrase=alpha bravo charlie delta echo foxtrot golf "
                "hotel india juliet kilo lima"
            ),
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(contains_secret_value_text(sample))

    def test_unknown_or_ambiguous_yaml_fails_closed(self) -> None:
        lines = self.stated_lines("Ambiguous")
        lines.append("unknown_field: value")
        self.write_card("rules/unknown-field", lines)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "unknown frontmatter field")

        self.brain.joinpath("rules/unknown-field.md").unlink()
        anchored = self.stated_lines("Anchored")
        anchored[0] = "title: Anchored &shared"
        self.write_card("rules/anchored", anchored)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "anchors, aliases, and tags")

    def test_malformed_sources_fail_closed(self) -> None:
        malformed = self.imported_lines("Malformed source")
        malformed.extend(
            [
                '    chat: "root-one:chat.json#message=2"',
            ]
        )
        self.write_card("rules/malformed-source", malformed)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "exactly one source kind")

        self.brain.joinpath("rules/malformed-source.md").unlink()
        secret = self.imported_lines("Secret source")
        secret[-1] = '  - file: "root-one:path?token=abcdefgh12345678"'
        self.write_card("rules/secret-source", secret)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "secret-like value")

    def test_unknown_source_alias_fails_closed(self) -> None:
        unknown = self.imported_lines("Unknown source alias")
        unknown[-1] = '  - file: "not-registered:path/to/source.md#Rule"'
        self.write_card("rules/unknown-source-alias", unknown)

        report = validate_cards(self.brain)
        self.assert_error_contains(report, "unknown source-root alias")
        self.assert_error_contains(report, "not-registered")

    def test_names_only_source_alias_cannot_authorize_imported_content(self) -> None:
        state_path = self.brain / "work" / "setup" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["scope"][0]["mode"] = "names-only"
        state["areas"]["source"]["coverage"] = "names-only"
        state_path.write_text(
            json.dumps(state, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        self.write_card(
            "rules/names-only-cannot-prove-content",
            self.imported_lines("Names only cannot prove content"),
        )

        report = validate_cards(self.brain)
        self.assert_error_contains(report, "lacks approved content coverage")

    def test_metadata_only_session_cannot_authorize_receipts(self) -> None:
        state_path = self.brain / "work" / "setup" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["session_history"]["host"]["coverage"] = "metadata-only"
        state_path.write_text(
            json.dumps(state, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        self.write_card(
            "rules/metadata-only-cannot-prove-receipt",
            self.inferred_lines("Metadata only cannot prove a receipt"),
        )

        report = validate_cards(self.brain)
        self.assert_error_contains(report, "lacks approved content coverage")

        state["session_history"]["host"]["coverage"] = "transcripts-reviewed"
        state_path.write_text(
            json.dumps(state, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        state_path.chmod(0o600)
        report = validate_cards(self.brain)
        self.assertTrue(report["ok"], report)

    def test_source_locator_cannot_escape_registered_root(self) -> None:
        escaping = self.imported_lines("Escaping source")
        escaping[-1] = '  - file: "root-one:../../outside/private.md"'
        self.write_card("rules/escaping-source", escaping)

        report = validate_cards(self.brain)
        self.assert_error_contains(
            report,
            "locator must stay inside its approved source root",
        )

    def test_source_locator_cannot_escape_through_symlink(self) -> None:
        source = self.brain.parent / "source"
        outside = self.brain.parent / "outside"
        source.mkdir()
        outside.mkdir()
        (source / "linked").symlink_to(outside, target_is_directory=True)
        escaping = self.imported_lines("Symlink source")
        escaping[-1] = '  - file: "root-one:linked/private.md"'
        self.write_card("rules/symlink-source", escaping)

        report = validate_cards(self.brain)
        self.assert_error_contains(
            report,
            "resolves outside its approved source root",
        )

    def test_git_source_accepts_registered_alias_and_revision(self) -> None:
        imported = self.imported_lines("Git source")
        imported[-1] = (
            '  - git: "root-one@0123456789abcdef:docs/decision.md#L20"'
        )
        self.write_card("rules/git-source", imported)

        report = validate_cards(self.brain)
        self.assertTrue(report["ok"], report)

    def test_malformed_receipts_and_high_risk_threshold_fail_closed(self) -> None:
        duplicate = self.inferred_lines(
            "Duplicate receipts",
            sessions=("host:same-session", "HOST:SAME-SESSION"),
        )
        self.write_card("rules/duplicate-receipts", duplicate)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "unique independent sessions")

        self.brain.joinpath("rules/duplicate-receipts.md").unlink()
        too_long = self.inferred_lines("Long quote")
        too_long[-1] = '    quote: "{}"'.format("q" * 201)
        self.write_card("rules/long-quote", too_long)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "exceeds 200 characters")

        self.brain.joinpath("rules/long-quote.md").unlink()
        high_risk = self.inferred_lines("High risk", risk="high")
        self.write_card("rules/high-risk", high_risk)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "at least 3 independent valid receipts")

    def test_real_calendar_dates_are_required(self) -> None:
        lines = self.imported_lines("Impossible date")
        lines[2] = "created: 2026-02-30"
        lines[4] = "imported_at: 2026-13-01"
        self.write_card("rules/impossible-date", lines)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "created must be a real calendar date")
        self.assert_error_contains(report, "imported_at must be a real calendar date")

    def test_supersession_cycle_is_rejected(self) -> None:
        a_lines = self.stated_lines(
            "Cycle A",
            extra=(
                "valid_from: 2026-01-01",
                "valid_to: 2026-02-01",
                "closed_at: 2026-02-01",
                "supersedes: rules/cycle-b",
                "superseded_by: rules/cycle-b",
            ),
        )
        b_lines = self.stated_lines(
            "Cycle B",
            extra=(
                "valid_from: 2026-02-01",
                "valid_to: 2026-03-01",
                "closed_at: 2026-03-01",
                "supersedes: rules/cycle-a",
                "superseded_by: rules/cycle-a",
            ),
        )
        self.write_card("rules/cycle-a", a_lines)
        self.write_card("rules/cycle-b", b_lines)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "supersession cycle")

    def test_supersession_branch_is_rejected(self) -> None:
        old_lines = self.stated_lines(
            "Branch old",
            extra=(
                "valid_to: 2026-02-01",
                "closed_at: 2026-02-01",
                "superseded_by: rules/branch-new-one",
            ),
        )
        new_one = self.stated_lines(
            "Branch new one",
            extra=(
                "valid_from: 2026-02-01",
                "supersedes: rules/branch-old",
            ),
        )
        new_two = self.stated_lines(
            "Branch new two",
            extra=(
                "valid_from: 2026-02-02",
                "supersedes: rules/branch-old",
            ),
        )
        self.write_card("rules/branch-old", old_lines)
        self.write_card("rules/branch-new-one", new_one)
        self.write_card("rules/branch-new-two", new_two)
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "supersession branching")

    def test_supersession_requires_matching_canonical_keys(self) -> None:
        old_lines = self.stated_lines(
            "Old wording",
            canonical_key="rules/old-key",
            extra=(
                "valid_to: 2026-02-01",
                "closed_at: 2026-02-01",
                "superseded_by: rules/new-wording",
            ),
        )
        new_lines = self.stated_lines(
            "New wording",
            canonical_key="rules/different-key",
            extra=(
                "valid_from: 2026-02-01",
                "supersedes: rules/old-wording",
            ),
        )
        self.write_card("rules/old-wording", old_lines)
        self.write_card("rules/new-wording", new_lines)

        report = validate_cards(self.brain)
        self.assert_error_contains(
            report,
            "supersession cards must keep the same canonical_key",
        )

    def test_supersession_requires_canonical_key_on_both_cards(self) -> None:
        old_lines = self.stated_lines(
            "Old without key",
            extra=(
                "valid_to: 2026-02-01",
                "closed_at: 2026-02-01",
                "superseded_by: rules/new-without-key",
            ),
        )
        new_lines = self.stated_lines(
            "New without key",
            extra=(
                "valid_from: 2026-02-01",
                "supersedes: rules/old-without-key",
            ),
        )
        self.write_card("rules/old-without-key", old_lines)
        self.write_card("rules/new-without-key", new_lines)

        report = validate_cards(self.brain)
        self.assert_error_contains(
            report,
            "both supersession cards require canonical_key",
        )

    def test_active_canonical_key_must_be_unique(self) -> None:
        self.write_card(
            "rules/duplicate-one",
            self.stated_lines("Duplicate one", canonical_key="shared/rule"),
        )
        self.write_card(
            "rules/duplicate-two",
            self.imported_lines("Duplicate two")
            + ['canonical_key: "shared/rule"'],
        )
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "duplicate active canonical_key")

    def test_origin_priority_orders_equal_matches(self) -> None:
        self.write_card("rules/stated-match", self.stated_lines("Stated match"))
        self.write_card("rules/imported-match", self.imported_lines("Imported match"))
        self.write_card("rules/inferred-match", self.inferred_lines("Inferred match"))
        self.write_card(
            "inferred/observed-match",
            self.observation_lines("Observed match"),
        )

        result = search_cards(self.brain, "shared needle", limit=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["limit"], 6)
        self.assertEqual(
            [item["origin"] for item in result["results"]],
            ["stated", "imported", "inferred", "observation"],
        )

    def test_closed_exact_match_returns_active_successor(self) -> None:
        old_lines = self.stated_lines(
            "Legacy wording",
            canonical_key="rules/tone",
            extra=(
                "valid_to: 2026-02-01",
                "closed_at: 2026-02-01",
                "superseded_by: rules/current-wording",
            ),
        )
        new_lines = self.stated_lines(
            "Current wording",
            canonical_key="rules/tone",
            extra=(
                "valid_from: 2026-02-01",
                "supersedes: rules/legacy-wording",
            ),
        )
        self.write_card("rules/legacy-wording", old_lines)
        self.write_card("rules/current-wording", new_lines)

        result = search_cards(self.brain, "Legacy wording")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"][0]["card"], "rules/current-wording")
        self.assertEqual(
            result["results"][0]["matched_via_closed"],
            "rules/legacy-wording",
        )
        self.assertFalse(result["results"][0]["closed"])

    def test_volatile_result_reports_staleness(self) -> None:
        lines = self.imported_lines("Volatile fact")
        lines.extend(
            [
                "volatile: true",
                "as_of: 2026-01-01",
                "stale_after_days: 30",
            ]
        )
        self.write_card("projects/volatile-fact", lines)

        stale = search_cards(
            self.brain,
            "Volatile fact",
            as_of="2026-02-15",
        )
        self.assertTrue(stale["ok"], stale)
        self.assertTrue(stale["results"][0]["stale"])
        self.assertEqual(stale["results"][0]["stale_after_days"], 30)

        fresh = search_cards(
            self.brain,
            "Volatile fact",
            as_of=dt.date(2026, 1, 20),
        )
        self.assertTrue(fresh["ok"], fresh)
        self.assertFalse(fresh["results"][0]["stale"])

    def test_examples_and_noncanonical_roots_are_not_searched(self) -> None:
        self.write_card(
            "examples/example-rule",
            self.stated_lines(
                "Hidden needle example",
                extra=("example: true",),
            ),
        )
        self.write_card(
            "inbox/pending-rule",
            self.stated_lines("Hidden needle inbox"),
        )
        self.write_card(
            "rules/marked-example",
            self.stated_lines(
                "Hidden needle marked",
                extra=("example: true",),
            ),
        )
        result = search_cards(self.brain, "hidden needle")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"], [])

        (self.brain / "inbox" / "malformed.md").write_text(
            "This excluded draft has no frontmatter.\n",
            encoding="utf-8",
        )
        (self.brain / "examples" / "malformed.md").write_text(
            "This excluded example has no frontmatter.\n",
            encoding="utf-8",
        )
        result = search_cards(self.brain, "hidden needle")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["results"], [])

    def test_broken_internal_link_and_non_kebab_path_fail_validation(self) -> None:
        self.write_card(
            "rules/Bad_Name",
            self.stated_lines("Broken link"),
            body="# Broken\n\nSee [[projects/missing-project]].",
        )
        report = validate_cards(self.brain)
        self.assert_error_contains(report, "ASCII kebab-case")
        self.assert_error_contains(report, "broken internal link")


if __name__ == "__main__":
    unittest.main()
