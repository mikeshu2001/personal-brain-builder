from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "skills" / "build-personal-brain" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from state_control import (  # noqa: E402
    AREA_COVERAGE,
    AREA_EXECUTION,
    TOP_LEVEL_STATES,
    StateTransitionError,
    StateValidationError,
    access_decision,
    active_consent_decision,
    has_active_consent,
    ready_gate_errors,
    transition_state,
    validate_state,
)


AT_1 = "2026-07-23T10:00:00+00:00"
AT_2 = "2026-07-23T10:01:00+00:00"


def base_state(brain_root: Path, *, state_name: str = "MAP_PERMISSION") -> dict[str, Any]:
    source = brain_root.parent / "work-source"
    return {
        "schema_version": 1,
        "installation_id": "installation-test-1",
        "brain_root": str(brain_root.resolve()),
        "owner_display_name": "Test Owner",
        "user_language": "en",
        "state": state_name,
        "substate": None,
        "created_at": AT_1,
        "entered_at": AT_1,
        "updated_at": AT_1,
        "next_action": "Continue the guided setup.",
        "blocking_issue": None,
        "compatibility": {
            "classification": "automatic",
            "manual_steps": [],
        },
        "manual_steps": [],
        "onboarding": {
            "schema_version": 1,
            "recorded_at": AT_1,
            "introduction_confirmed": True,
            "work_summary_saved": True,
            "work_summary": "Builds deterministic synthetic systems.",
            "desired_outcome": "One safe synthetic memory.",
            "computer_use": "private",
            "never_open": [],
            "read_not_store": [],
            "processing": {
                "mode": "local",
                "note": "Synthetic processing is local.",
                "acknowledged": True,
            },
            "compatibility": {
                "classification": "automatic",
                "manual_steps": [],
            },
        },
        "consents": [],
        "scope": [],
        "areas": {
            "work": {
                "id": "work",
                "label": "Work",
                "paths": [str(source.resolve())],
                "coverage": "content-reviewed",
                "execution": "closed",
                "cursor": None,
                "cards": ["projects/example.md"],
                "journal": "journal/2026-07-23-work.md",
                "saved_revision": "synthetic-revision-1",
                "pre_fingerprints": {
                    "sha256": "a" * 64,
                    "roots": [str(source.resolve())],
                },
                "post_fingerprints": {
                    "sha256": "a" * 64,
                    "verified": True,
                    "roots": [str(source.resolve())],
                },
            }
        },
        "session_history": {},
        "connections": {
            "codex": {
                "target": str((brain_root.parent / "AGENTS.md").resolve()),
                "selected": True,
                "status": "verified",
                "evidence": "Fresh synthetic conversation retrieved the canary.",
                "test_result": {"ok": True},
            }
        },
        "checks": {
            "structure": {
                "status": "pass",
                "evidence": "Synthetic structure check passed.",
                "checked_at": AT_1,
            }
        },
        "open_conflicts": [],
        "expected_revision": "synthetic-revision-1",
        "authorized_write_set": [],
    }


class StateControlTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="state-control-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.sandbox = Path(self.temp_dir.name)
        self.brain = self.sandbox / "brain"
        self.brain.mkdir()

    def test_exact_top_level_states_and_map_permission_cannot_skip_to_ready(self) -> None:
        self.assertEqual(
            TOP_LEVEL_STATES,
            (
                "DISCOVER_OR_RESUME",
                "COMPATIBILITY_CHECK",
                "INTRO",
                "PROFILE_SEED",
                "SAFETY_AND_PERMISSIONS",
                "BRAIN_LOCATION",
                "BRAIN_CREATE",
                "MAP_PERMISSION",
                "METADATA_MAP",
                "SCOPE_REVIEW",
                "DEEP_AUDIT_GATE",
                "CONTOUR_LOOP",
                "SESSIONS_PERMISSION",
                "SESSION_HISTORY_AUDIT",
                "CONSOLIDATION",
                "CONFLICT_REVIEW",
                "CORE_REVIEW",
                "LIBRARY_VALIDATION",
                "CONNECTION_REVIEW",
                "CONNECTION_APPLY",
                "ACCEPTANCE_TESTING",
                "RECOVERY_COPY_REVIEW",
                "HANDOFF",
                "READY",
                "PAUSED",
                "FAILED_CLOSED",
            ),
        )
        state = base_state(self.brain)
        with self.assertRaisesRegex(
            StateTransitionError,
            "MAP_PERMISSION -> READY",
        ):
            transition_state(
                state,
                "READY",
                self.brain,
                required_check_ids=("structure",),
            )

    def test_pause_exact_resume_and_failed_closed_recovery(self) -> None:
        state = base_state(self.brain)

        paused = transition_state(state, "PAUSED", self.brain, now=AT_2)
        self.assertEqual(paused["resume_from"], "MAP_PERMISSION")
        with self.assertRaisesRegex(StateTransitionError, "resume only"):
            transition_state(paused, "METADATA_MAP", self.brain)
        resumed = transition_state(paused, "MAP_PERMISSION", self.brain)
        self.assertNotIn("resume_from", resumed)

        failed = transition_state(
            resumed,
            "FAILED_CLOSED",
            self.brain,
            blocking_issue="The durable pointer disagrees with state.",
        )
        self.assertEqual(failed["failed_from"], "MAP_PERMISSION")
        with self.assertRaisesRegex(StateTransitionError, "recovery=True"):
            transition_state(failed, "DISCOVER_OR_RESUME", self.brain)

        recovering = transition_state(
            failed,
            "DISCOVER_OR_RESUME",
            self.brain,
            recovery=True,
        )
        self.assertEqual(recovering["resume_from"], "MAP_PERMISSION")
        with self.assertRaisesRegex(StateTransitionError, "exact recorded"):
            transition_state(
                recovering,
                "INTRO",
                self.brain,
                recovery=True,
            )
        with self.assertRaisesRegex(StateTransitionError, "exact recorded"):
            transition_state(
                recovering,
                "COMPATIBILITY_CHECK",
                self.brain,
                recovery=True,
            )
        recovered = transition_state(
            recovering,
            "MAP_PERMISSION",
            self.brain,
            recovery=True,
        )
        self.assertEqual(recovered["state"], "MAP_PERMISSION")
        self.assertNotIn("resume_from", recovered)

    def test_latest_exact_consent_revocation_blocks_access(self) -> None:
        source = self.sandbox / "source"
        source.mkdir()
        state = base_state(self.brain)
        state["scope"] = [
            {
                "path": str(source.resolve()),
                "mode": "content",
                "label": "Source",
                "reason": "Owner selected it.",
                "approved_at": AT_1,
            }
        ]
        state["consents"] = [
            {
                "kind": "content-audit",
                "decision": "approved",
                "scope": [str(source.resolve())],
                "note": "",
                "at": AT_1,
            },
            {
                "kind": "content-audit",
                "decision": "revoked",
                "scope": [str(source.resolve())],
                "note": "Owner changed their mind.",
                "at": AT_2,
            },
        ]
        validate_state(state, self.brain)

        self.assertEqual(
            active_consent_decision(state, "content-audit", [source]),
            "revoked",
        )
        self.assertFalse(has_active_consent(state, "content-audit", [source]))
        decision = access_decision(state, source / "document.md", "content")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.consent_decision, "revoked")

    def test_most_specific_child_exclude_overrides_permitted_parent(self) -> None:
        source = self.sandbox / "source"
        private = source / "private"
        sibling = source / "public"
        private.mkdir(parents=True)
        sibling.mkdir()
        state = base_state(self.brain)
        state["scope"] = [
            {
                "path": str(source.resolve()),
                "mode": "content",
                "label": "Source",
                "reason": "Owner selected it.",
                "approved_at": AT_1,
            },
            {
                "path": str(private.resolve()),
                "mode": "exclude",
                "label": "Private",
                "reason": "Owner excluded it.",
                "approved_at": AT_2,
            },
        ]
        state["consents"] = [
            {
                "kind": "content-audit",
                "decision": "approved",
                "scope": [str(source.resolve())],
                "note": "",
                "at": AT_1,
            }
        ]
        validate_state(state, self.brain)

        permitted = access_decision(state, sibling / "read-me.md", "content")
        excluded = access_decision(state, private / "do-not-read.md", "content")
        self.assertTrue(permitted.allowed)
        self.assertFalse(excluded.allowed)
        self.assertEqual(excluded.matched_mode, "exclude")

    def test_area_coverage_and_execution_are_distinct_dimensions(self) -> None:
        self.assertEqual(
            AREA_COVERAGE,
            {
                "content-reviewed",
                "names-only",
                "excluded-by-user",
                "unavailable",
                "blocked-sensitive",
                "needs-recheck",
            },
        )
        self.assertEqual(AREA_EXECUTION, {"pending", "in-progress", "closed"})

        valid = base_state(self.brain)
        validate_state(valid, self.brain)

        invalid_coverage = copy.deepcopy(valid)
        invalid_coverage["areas"]["work"]["coverage"] = "closed"
        with self.assertRaisesRegex(StateValidationError, "coverage"):
            validate_state(invalid_coverage, self.brain)

        impossible_pair = copy.deepcopy(valid)
        impossible_pair["areas"]["work"]["coverage"] = "needs-recheck"
        with self.assertRaisesRegex(StateValidationError, "needs recheck"):
            validate_state(impossible_pair, self.brain)

    def test_folder_permission_transition_prerequisites(self) -> None:
        source = self.sandbox / "mapped-source"
        source.mkdir()
        state = base_state(self.brain, state_name="MAP_PERMISSION")
        state["areas"] = {}
        state["connections"] = {}

        with self.assertRaisesRegex(
            StateTransitionError,
            "at least one exact folder",
        ):
            transition_state(state, "METADATA_MAP", self.brain)

        state["scope"] = [
            {
                "path": str(source.resolve()),
                "mode": "names-only",
                "label": "Mapped source",
                "reason": "The owner selected it.",
                "approved_at": AT_1,
                "consent_scope": [str(source.resolve())],
            }
        ]
        with self.assertRaisesRegex(
            StateTransitionError,
            "lacks active exact metadata-map consent",
        ):
            transition_state(state, "METADATA_MAP", self.brain)

        state["consents"] = [
            {
                "kind": "metadata-map",
                "decision": "approved",
                "scope": [str(source.resolve())],
                "note": "",
                "at": AT_1,
            }
        ]
        advanced = transition_state(state, "METADATA_MAP", self.brain)
        self.assertEqual(advanced["state"], "METADATA_MAP")

        deep = copy.deepcopy(state)
        deep["state"] = "DEEP_AUDIT_GATE"
        deep["scope"][0]["mode"] = "content"
        deep["consents"] = []
        with self.assertRaisesRegex(
            StateTransitionError,
            "lacks active exact content-audit consent",
        ):
            transition_state(deep, "CONTOUR_LOOP", self.brain)

        deep["consents"] = [
            {
                "kind": "content-audit",
                "decision": "approved",
                "scope": [str(source.resolve())],
                "note": "",
                "at": AT_1,
            }
        ]
        advanced = transition_state(deep, "CONTOUR_LOOP", self.brain)
        self.assertEqual(advanced["state"], "CONTOUR_LOOP")

    def test_contour_and_session_transition_prerequisites(self) -> None:
        state = base_state(self.brain, state_name="CONTOUR_LOOP")
        state["areas"]["work"]["coverage"] = "needs-recheck"
        state["areas"]["work"]["execution"] = "in-progress"
        with self.assertRaisesRegex(
            StateTransitionError,
            "area 'work' is not closed",
        ) as blocked:
            transition_state(state, "SESSIONS_PERMISSION", self.brain)
        self.assertIn("still needs recheck", str(blocked.exception))

        state["areas"]["work"]["coverage"] = "content-reviewed"
        state["areas"]["work"]["execution"] = "closed"
        unmapped = self.sandbox / "unmapped-source"
        unmapped.mkdir()
        missing_coverage = copy.deepcopy(state)
        missing_coverage["scope"] = [
            {
                "path": str(unmapped.resolve()),
                "mode": "names-only",
                "label": "Unmapped source",
                "reason": "Synthetic approved root.",
                "approved_at": AT_1,
                "consent_scope": [str(unmapped.resolve())],
            }
        ]
        missing_coverage["consents"] = [
            {
                "kind": "metadata-map",
                "decision": "approved",
                "scope": [str(unmapped.resolve())],
                "note": "",
                "at": AT_1,
            }
        ]
        with self.assertRaisesRegex(
            StateTransitionError,
            "lacks an area coverage record",
        ):
            transition_state(
                missing_coverage,
                "SESSIONS_PERMISSION",
                self.brain,
            )
        advanced = transition_state(state, "SESSIONS_PERMISSION", self.brain)
        self.assertEqual(advanced["state"], "SESSIONS_PERMISSION")

        sessions = copy.deepcopy(state)
        sessions["state"] = "SESSIONS_PERMISSION"
        sessions["session_history"] = {}
        with self.assertRaisesRegex(
            StateTransitionError,
            "no session-history source has active exact consent",
        ):
            transition_state(sessions, "SESSION_HISTORY_AUDIT", self.brain)

        session_path = self.sandbox / "session-export"
        session_path.mkdir()
        sessions["session_history"] = {
            "host": {
                "path": str(session_path.resolve()),
                "execution": "pending",
                "coverage": "metadata-only",
            }
        }
        sessions["consents"] = [
            {
                "kind": "session-history-metadata",
                "decision": "approved",
                "scope": [str(session_path.resolve())],
                "note": "",
                "at": AT_1,
            },
            {
                "kind": "session-history",
                "decision": "approved",
                "scope": [str(session_path.resolve())],
                "note": "",
                "at": AT_1,
            }
        ]
        content_without_metadata = copy.deepcopy(sessions)
        content_without_metadata["consents"] = [
            content_without_metadata["consents"][1]
        ]
        with self.assertRaisesRegex(
            StateTransitionError,
            "lacks prior exact metadata consent",
        ):
            transition_state(
                content_without_metadata,
                "SESSION_HISTORY_AUDIT",
                self.brain,
            )
        advanced = transition_state(
            sessions,
            "SESSION_HISTORY_AUDIT",
            self.brain,
        )
        self.assertEqual(advanced["state"], "SESSION_HISTORY_AUDIT")

        audit = copy.deepcopy(sessions)
        audit["state"] = "SESSION_HISTORY_AUDIT"
        audit["session_history"]["host"]["coverage"] = "needs-recheck"
        audit["session_history"]["host"]["execution"] = "in-progress"
        with self.assertRaisesRegex(
            StateTransitionError,
            "session source 'host' is not closed",
        ) as blocked:
            transition_state(audit, "CONSOLIDATION", self.brain)
        self.assertIn("still needs recheck", str(blocked.exception))

        audit["session_history"]["host"]["coverage"] = "transcripts-reviewed"
        audit["session_history"]["host"]["execution"] = "closed"
        advanced = transition_state(audit, "CONSOLIDATION", self.brain)
        self.assertEqual(advanced["state"], "CONSOLIDATION")

    def test_connection_and_acceptance_transition_prerequisites(self) -> None:
        state = base_state(self.brain, state_name="CONNECTION_REVIEW")
        state["connections"] = {}
        with self.assertRaisesRegex(
            StateTransitionError,
            "no connection target was selected",
        ):
            transition_state(state, "CONNECTION_APPLY", self.brain)

        target = self.sandbox / "AGENTS.md"
        state["connections"] = {
            str(target.resolve()): {
                "target": str(target.resolve()),
                "selected": True,
                "status": "pending-approval",
            }
        }
        with self.assertRaisesRegex(
            StateTransitionError,
            "lacks active exact consent",
        ):
            transition_state(state, "CONNECTION_APPLY", self.brain)

        state["consents"] = [
            {
                "kind": "connection",
                "decision": "approved",
                "scope": [str(target.resolve())],
                "note": "",
                "at": AT_1,
            }
        ]
        advanced = transition_state(state, "CONNECTION_APPLY", self.brain)
        self.assertEqual(advanced["state"], "CONNECTION_APPLY")

        acceptance = copy.deepcopy(state)
        acceptance["state"] = "ACCEPTANCE_TESTING"
        acceptance["connections"][str(target.resolve())]["status"] = "connected"
        with self.assertRaisesRegex(
            StateTransitionError,
            "required check 'second-check' is missing",
        ) as blocked:
            transition_state(
                acceptance,
                "RECOVERY_COPY_REVIEW",
                self.brain,
                required_check_ids=("structure", "second-check"),
            )
        self.assertIn("is not verified", str(blocked.exception))

        acceptance["checks"]["second-check"] = {
            "status": "pass",
            "evidence": "Synthetic second check passed.",
            "checked_at": AT_2,
        }
        acceptance["connections"][str(target.resolve())]["status"] = "verified"
        acceptance["connections"][str(target.resolve())][
            "evidence"
        ] = "Fresh synthetic conversation retrieved the canary."
        advanced = transition_state(
            acceptance,
            "RECOVERY_COPY_REVIEW",
            self.brain,
            required_check_ids=("structure", "second-check"),
        )
        self.assertEqual(advanced["state"], "RECOVERY_COPY_REVIEW")
        with self.assertRaisesRegex(
            StateTransitionError,
            "has not recorded a backup decision",
        ):
            transition_state(advanced, "HANDOFF", self.brain)
        advanced["consents"].append(
            {
                "kind": "recovery-copy",
                "decision": "declined",
                "scope": [str(self.brain.resolve())],
                "note": "Synthetic owner declined a recovery copy.",
                "at": AT_2,
            }
        )
        handoff = transition_state(advanced, "HANDOFF", self.brain)
        self.assertEqual(handoff["state"], "HANDOFF")

    def test_full_ready_gate_and_ready_transition(self) -> None:
        state = base_state(self.brain, state_name="HANDOFF")
        state["open_conflicts"] = [
            {"id": "non-blocking-note", "blocking": False, "status": "open"}
        ]
        self.assertEqual(ready_gate_errors(state, ("structure",)), [])

        ready = transition_state(
            state,
            "READY",
            self.brain,
            required_check_ids=("structure",),
            now=AT_2,
        )
        self.assertEqual(ready["state"], "READY")

        blocked = copy.deepcopy(state)
        blocked["areas"]["work"]["coverage"] = "names-only"
        blocked["areas"]["work"]["execution"] = "in-progress"
        blocked["checks"]["structure"]["status"] = "fail"
        blocked["checks"]["optional"] = {
            "status": "skipped",
            "evidence": "Not run.",
            "checked_at": AT_2,
        }
        blocked["connections"]["codex"]["status"] = "connected"
        blocked["open_conflicts"].append(
            {"id": "identity-conflict", "blocking": True, "status": "open"}
        )

        errors = ready_gate_errors(
            blocked,
            ("structure", "missing-required-check"),
        )
        rendered = "\n".join(errors)
        self.assertIn("area 'work' is not closed", rendered)
        self.assertIn("check 'structure' is fail", rendered)
        self.assertIn("check 'optional' is skipped", rendered)
        self.assertIn("required check 'missing-required-check' is missing", rendered)
        self.assertIn("connection 'codex' is not verified", rendered)
        self.assertIn("blocking conflict 'identity-conflict' is open", rendered)


if __name__ == "__main__":
    unittest.main()
