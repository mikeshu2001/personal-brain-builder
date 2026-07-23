from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BRAINCTL = REPO_ROOT / "skills" / "build-personal-brain" / "scripts" / "brainctl.py"
REQUIRED_INSTALLATION_CHECKS = (
    "library-structure",
    "source-links-valid",
    "no-secret-values",
    "source-integrity",
    "coverage-accounting",
    "active-version-routing",
    "closed-card-blocked",
    "origin-priority",
    "freshness-warning",
    "provenance-explanation",
    "excluded-scope-blocked",
    "unresolved-conflict-behavior",
    "prompt-injection-treated-as-data",
    "synthetic-secret-stop",
    "fresh-session-general",
    "fresh-session-project",
    "cross-project-routing",
    "current-instruction-wins",
    "remember-replace-cleanup",
    "pause-resume",
    "connection-bounded-reversible",
    "history-undo-recovery",
    "temporary-test-cleanup",
)
SCRIPTS_DIRECTORY = BRAINCTL.parent
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import portable_history  # noqa: E402


class SimulatedKill(BaseException):
    """Leave durable artifacts as a real SIGKILL would."""


class BrainCtlCliTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="brainctl-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.sandbox = Path(self.temp_dir.name)
        self.fake_home = self.sandbox / "synthetic-home"
        self.fake_home.mkdir()
        self.env = os.environ.copy()
        self.env.update({"HOME": str(self.fake_home)})
        self.brain = self.sandbox / "brain"
        self.pointer = self.sandbox / "brain-pointer.json"
        preview_token = self.creation_preview_token(self.brain, self.pointer)
        self.init_payload, _ = self.run_brainctl(
            "init",
            "--brain-root",
            str(self.brain),
            "--owner",
            "Synthetic Owner",
            "--locale",
            "en",
            "--pointer-file",
            str(self.pointer),
            "--preview-token",
            preview_token,
            "--approval-note",
            "Synthetic owner approved this exact private location.",
        )
        self.run_brainctl(
            "record",
            "onboarding",
            "--brain-root",
            str(self.brain),
            "--work-summary",
            "Builds synthetic software systems for deterministic tests.",
            "--desired-outcome",
            "A safe synthetic cross-project memory.",
            "--computer-use",
            "private",
            "--processing-mode",
            "local",
            "--processing-note",
            "Synthetic fixture processing stays inside the test process.",
            "--capability",
            "automatic",
            "--never-open",
            "synthetic credential stores",
            "--read-not-store",
            "synthetic private drafts",
            "--introduction-confirmed",
            "--processing-acknowledged",
        )

    def run_brainctl(
        self,
        *arguments: str,
        expected_returncode: int = 0,
    ) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
        result = subprocess.run(
            [sys.executable, str(BRAINCTL), *arguments],
            cwd=self.sandbox,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                "brainctl did not return one JSON object\n"
                f"command: {arguments!r}\n"
                f"returncode: {result.returncode}\n"
                f"stdout: {result.stdout!r}\n"
                f"stderr: {result.stderr!r}\n"
                f"json error: {exc}"
            )
        self.assertEqual(
            result.returncode,
            expected_returncode,
            "unexpected brainctl return code\n"
            f"command: {arguments!r}\n"
            f"payload: {payload!r}\n"
            f"stderr: {result.stderr!r}",
        )
        return payload, result

    def creation_preview_token(self, path: Path, pointer: Path) -> str:
        preview, _ = self.run_brainctl(
            "doctor",
            "--path",
            str(path),
            "--pointer-file",
            str(pointer),
        )
        return preview["creation_preview_token"]

    def approve(self, kind: str, *paths: Path) -> None:
        self.run_brainctl(
            "record",
            "consent",
            "--brain-root",
            str(self.brain),
            "--kind",
            kind,
            "--decision",
            "approved",
            "--scope",
            *(str(path) for path in paths),
            "--note",
            "Synthetic test approval",
        )

    def select_connection(self, target: Path) -> None:
        self.run_brainctl(
            "record",
            "connection",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            "--status",
            "pending-approval",
        )

    def set_stage_for_test(self, stage: str) -> None:
        path = self.brain / "work" / "setup" / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["state"] = stage
        state["substate"] = None
        state["next_action"] = "Continue the synthetic test."
        state["blocking_issue"] = None
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def valid_rule_bytes(title: str, summary: str, body: str) -> bytes:
        return (
            "---\n"
            f'title: "{title}"\n'
            "type: rule\n"
            "created: 2026-01-15\n"
            "origin: stated\n"
            "stated_in: session:synthetic-owner\n"
            f'summary: "{summary}"\n'
            "---\n\n"
            f"# {title}\n\n"
            f"{body}\n"
        ).encode("utf-8")

    def test_init_status_and_save_work_with_empty_path_without_git(self) -> None:
        self.env["PATH"] = ""
        brain = self.sandbox / "brain-without-system-commands"
        pointer = self.sandbox / "brain-without-system-commands-pointer.json"
        preview_token = self.creation_preview_token(brain, pointer)

        initialized, _ = self.run_brainctl(
            "init",
            "--brain-root",
            str(brain),
            "--owner",
            "No Git Owner",
            "--locale",
            "en",
            "--pointer-file",
            str(pointer),
            "--preview-token",
            preview_token,
            "--approval-note",
            "Synthetic owner approved this exact private location.",
        )
        self.assertTrue(initialized["ok"])
        self.assertTrue((brain / ".brain-history").is_dir())
        self.assertFalse((brain / ".git").exists())

        initial_status, _ = self.run_brainctl(
            "status",
            "--brain-root",
            str(brain),
        )
        self.assertTrue(initial_status["ok"])
        self.assertTrue(initial_status["history_clean"])

        card = brain / "rules" / "portable-history.md"
        card.write_bytes(
            self.valid_rule_bytes(
                "Portable history",
                "The private memory keeps its own local history",
                "Saving must not require an external version-control program.",
            )
        )
        saved, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(brain),
            "--paths",
            "rules/portable-history.md",
            "--summary",
            "add portable history rule",
            "--reason",
            "exercise the dependency-free command path",
        )
        self.assertTrue(saved["ok"])

        final_status, _ = self.run_brainctl(
            "status",
            "--brain-root",
            str(brain),
        )
        self.assertTrue(final_status["ok"])
        self.assertTrue(final_status["history_clean"])
        self.assertEqual(final_status["history"]["head"], saved["revision"])

    def test_onboarding_choices_are_durable_and_required_before_mapping(self) -> None:
        state_path = self.brain / "work" / "setup" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["onboarding"] = None
        state["compatibility"] = {}
        state["manual_steps"] = []
        state["next_action"] = "Finish the confirmed introduction."
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        source = self.sandbox / "onboarding-source"
        source.mkdir()

        blocked, _ = self.run_brainctl(
            "record",
            "consent",
            "--brain-root",
            str(self.brain),
            "--kind",
            "metadata-map",
            "--decision",
            "approved",
            "--scope",
            str(source),
            "--note",
            "Must remain blocked until onboarding is durable.",
            expected_returncode=2,
        )
        self.assertIn("onboarding choices", blocked["error"])

        recorded, _ = self.run_brainctl(
            "record",
            "onboarding",
            "--brain-root",
            str(self.brain),
            "--work-summary",
            "Coordinates two synthetic work areas.",
            "--desired-outcome",
            "One tested memory across synthetic assistants.",
            "--computer-use",
            "shared",
            "--processing-mode",
            "online-service",
            "--processing-note",
            "Approved text may be processed by the selected assistant service.",
            "--capability",
            "manual-connection",
            "--never-open",
            "health topic",
            "credential folder",
            "--read-not-store",
            "private planning notes",
            "--manual-steps",
            "Open one fresh conversation for verification.",
            "--introduction-confirmed",
            "--processing-acknowledged",
        )
        self.assertTrue(recorded["onboarding_complete"])
        self.assertEqual(recorded["never_open_count"], 2)
        durable = json.loads(state_path.read_text(encoding="utf-8"))["onboarding"]
        self.assertEqual(durable["computer_use"], "shared")
        self.assertEqual(durable["never_open"], ["health topic", "credential folder"])
        self.assertEqual(
            durable["read_not_store"],
            ["private planning notes"],
        )

    def test_init_then_validate(self) -> None:
        self.assertTrue(self.init_payload["ok"])
        self.assertEqual(Path(self.init_payload["brain_root"]), self.brain.resolve())
        self.assertEqual(Path(self.init_payload["pointer_file"]), self.pointer.resolve())
        self.assertTrue((self.brain / ".brain-history").is_dir())
        self.assertFalse((self.brain / ".git").exists())
        self.assertTrue(self.pointer.is_file())
        pointer_payload = json.loads(self.pointer.read_text(encoding="utf-8"))
        self.assertNotIn("owner", pointer_payload)
        state_payload = json.loads(
            (self.brain / "work" / "setup" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state_payload["consents"][0]["kind"], "memory-location")
        self.assertEqual(state_payload["consents"][0]["decision"], "approved")

        profile = (self.brain / "profile" / "owner.md").read_text(encoding="utf-8")
        self.assertIn("Synthetic Owner", profile)
        self.assertNotIn("{{OWNER_NAME}}", profile)

        payload, _ = self.run_brainctl(
            "validate",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["cards_checked"], 2)
        self.assertEqual(payload["errors"], [])
        history, _ = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(history["clean"])
        self.assertTrue(history["revision_matches_state"])

    def test_full_guided_cli_path_reaches_ready_and_runtime_is_self_contained(
        self,
    ) -> None:
        source = self.sandbox / "guided-source"
        source.mkdir()
        (source / "visible.txt").write_text(
            "Synthetic source content is not opened during the names-only path.",
            encoding="utf-8",
        )
        self.approve("metadata-map", source)
        self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(source),
            "--mode",
            "names-only",
            "--label",
            "Guided source",
        )

        def advance(stage: str, action: str) -> None:
            result, _ = self.run_brainctl(
                "state",
                "--brain-root",
                str(self.brain),
                "--stage",
                stage,
                "--next-action",
                action,
            )
            self.assertEqual(result["to"], stage)

        advance("METADATA_MAP", "Build the approved names-only map.")
        self.run_brainctl(
            "inventory",
            "--brain-root",
            str(self.brain),
            "--roots",
            str(source),
        )
        self.run_brainctl(
            "record",
            "area",
            "--brain-root",
            str(self.brain),
            "--id",
            "guided-source",
            "--label",
            "Guided source",
            "--paths",
            str(source),
            "--status",
            "names-only",
        )
        for stage, action in (
            ("DEEP_AUDIT_GATE", "Review content choices."),
            ("CONTOUR_LOOP", "Close every approved source area."),
            ("SESSIONS_PERMISSION", "Review the conversation-history choice."),
            ("CONSOLIDATION", "Consolidate the approved knowledge."),
            ("CONFLICT_REVIEW", "Review important conflicts."),
            ("CORE_REVIEW", "Review the compact owner profile."),
        ):
            advance(stage, action)

        profile = self.brain / "profile" / "owner.md"
        profile.write_text(
            profile.read_text(encoding="utf-8")
            .replace(
                "To be completed during onboarding.",
                "The owner builds synthetic software systems and reviews their safety.",
            )
            .replace(
                "Other confirmed preferences will be added during onboarding.",
                "Use concise explanations, explicit boundaries, and verifiable outcomes.",
            )
            .replace(
                "Safety choices will be added during onboarding.",
                "Never open synthetic credential stores; do not retain private drafts.",
            ),
            encoding="utf-8",
        )
        self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "profile/owner.md",
            "--summary",
            "complete the guided owner profile",
            "--reason",
            "the synthetic owner confirmed the onboarding answers",
        )
        self.run_brainctl(
            "record",
            "core",
            "--brain-root",
            str(self.brain),
            "--note",
            "Synthetic owner reviewed the complete compact profile.",
        )
        for stage, action in (
            ("LIBRARY_VALIDATION", "Validate the complete memory."),
            ("CONNECTION_REVIEW", "Review optional assistant connections."),
            ("ACCEPTANCE_TESTING", "Run the release checks."),
        ):
            advance(stage, action)

        for check_id in REQUIRED_INSTALLATION_CHECKS:
            self.run_brainctl(
                "record",
                "check",
                "--brain-root",
                str(self.brain),
                "--id",
                check_id,
                "--status",
                "pass",
                "--evidence",
                f"Synthetic isolated evidence passed for {check_id}.",
            )
        advance(
            "RECOVERY_COPY_REVIEW",
            "Record the recovery-copy decision.",
        )
        self.run_brainctl(
            "record",
            "consent",
            "--brain-root",
            str(self.brain),
            "--kind",
            "recovery-copy",
            "--decision",
            "declined",
            "--scope",
            str(self.brain),
            "--note",
            "Synthetic owner chose to continue without a separate copy.",
        )
        advance("HANDOFF", "Show the final coverage report.")
        advance("READY", "Use the finished memory.")

        runtime = (
            self.brain / "system" / "runtime" / "scripts" / "brainctl.py"
        )
        for arguments in (
            ("status", "--brain-root", str(self.brain)),
            ("validate", "--brain-root", str(self.brain)),
            ("history", "status", "--brain-root", str(self.brain)),
            (
                "search",
                "--brain-root",
                str(self.brain),
                "--query",
                "synthetic owner",
            ),
        ):
            result = subprocess.run(
                [sys.executable, str(runtime), *arguments],
                cwd=self.brain,
                env=self.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"runtime command failed: {arguments!r}\n{result.stdout}\n{result.stderr}",
            )
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_concurrent_init_reserves_one_exact_pointer(self) -> None:
        first_brain = self.sandbox / "concurrent-brain-a"
        second_brain = self.sandbox / "concurrent-brain-b"
        shared_pointer = self.sandbox / "concurrent-pointer.json"
        first_token = self.creation_preview_token(first_brain, shared_pointer)
        second_token = self.creation_preview_token(second_brain, shared_pointer)

        commands = []
        for brain, token, owner in (
            (first_brain, first_token, "Concurrent Owner A"),
            (second_brain, second_token, "Concurrent Owner B"),
        ):
            commands.append(
                [
                    sys.executable,
                    str(BRAINCTL),
                    "init",
                    "--brain-root",
                    str(brain),
                    "--owner",
                    owner,
                    "--locale",
                    "en",
                    "--pointer-file",
                    str(shared_pointer),
                    "--preview-token",
                    token,
                    "--approval-note",
                    "Synthetic owner approved this exact private location.",
                ]
            )
        processes = [
            subprocess.Popen(
                command,
                cwd=self.sandbox,
                env=self.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for command in commands
        ]
        results = [process.communicate(timeout=30) for process in processes]
        return_codes = [process.returncode for process in processes]
        self.assertEqual(sorted(return_codes), [0, 2], results)
        pointer = json.loads(shared_pointer.read_text(encoding="utf-8"))
        selected = Path(pointer["brain_root"])
        self.assertIn(selected, {first_brain.resolve(), second_brain.resolve()})
        self.assertTrue((selected / "work" / "setup" / "state.json").is_file())
        unselected = (
            second_brain.resolve()
            if selected == first_brain.resolve()
            else first_brain.resolve()
        )
        self.assertFalse(unselected.exists())

    def test_stale_init_claim_requires_preview_token_to_clear(self) -> None:
        pointer = self.sandbox / "stale-init-pointer.json"
        claim = {
            "schema_version": 1,
            "status": "pending-creation",
            "claim_id": "a" * 32,
            "created_at": "2026-07-23T10:00:00+00:00",
            "pid": 999_999_999,
            "host": platform.node() or "local-host-unknown",
            "brain_root": str((self.sandbox / "abandoned-brain").resolve()),
            "owner": "Synthetic Owner",
            "locale": "en",
            "preview_token": "b" * 64,
        }
        pointer.write_text(
            json.dumps(claim, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pointer.chmod(0o600)

        preview, _ = self.run_brainctl(
            "init-claim",
            "clear-preview",
            "--pointer-file",
            str(pointer),
        )
        blocked, _ = self.run_brainctl(
            "init-claim",
            "clear",
            "--pointer-file",
            str(pointer),
            "--token",
            "0" * 64,
            expected_returncode=2,
        )
        self.assertIn("token", blocked["error"].casefold())
        self.assertTrue(pointer.exists())
        cleared, _ = self.run_brainctl(
            "init-claim",
            "clear",
            "--pointer-file",
            str(pointer),
            "--token",
            preview["clear_token"],
        )
        self.assertTrue(cleared["cleared"])
        self.assertFalse(pointer.exists())

    def test_creation_preview_is_bound_to_pointer_and_owner_is_required(self) -> None:
        brain = self.sandbox / "bound-preview-brain"
        pointer_a = self.sandbox / "pointer-a.json"
        pointer_b = self.sandbox / "pointer-b.json"
        token = self.creation_preview_token(brain, pointer_a)
        wrong_pointer, _ = self.run_brainctl(
            "init",
            "--brain-root",
            str(brain),
            "--owner",
            "Bound Owner",
            "--locale",
            "en",
            "--pointer-file",
            str(pointer_b),
            "--preview-token",
            token,
            "--approval-note",
            "Synthetic owner approved this exact private location.",
            expected_returncode=2,
        )
        self.assertIn("preview", wrong_pointer["error"].casefold())
        self.assertFalse(brain.exists())
        self.assertFalse(pointer_b.exists())

        empty_owner, _ = self.run_brainctl(
            "init",
            "--brain-root",
            str(brain),
            "--owner",
            " ",
            "--locale",
            "en",
            "--pointer-file",
            str(pointer_a),
            "--preview-token",
            token,
            "--approval-note",
            "Synthetic owner approved this exact private location.",
            expected_returncode=2,
        )
        self.assertIn("owner", empty_owner["error"].casefold())
        self.assertFalse(pointer_a.exists())

    def test_repeated_init_with_same_pointer_and_root_is_idempotent(self) -> None:
        before_head = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )[0]["head"]
        before_pointer = self.pointer.read_bytes()
        state_path = self.brain / "work" / "setup" / "state.json"
        events_path = self.brain / "work" / "setup" / "events.jsonl"
        before_state = state_path.read_bytes()
        before_events = events_path.read_bytes()

        payload, _ = self.run_brainctl(
            "init",
            "--brain-root",
            str(self.brain),
            "--owner",
            "Synthetic Owner",
            "--locale",
            "en",
            "--pointer-file",
            str(self.pointer),
            "--preview-token",
            "not-used-for-existing-memory",
            "--approval-note",
            "Repeated exact initialization.",
        )

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["already_exists"])
        self.assertEqual(payload["brain_id"], self.init_payload["brain_id"])
        after_head = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )[0]["head"]
        self.assertEqual(after_head, before_head)
        self.assertEqual(self.pointer.read_bytes(), before_pointer)
        self.assertEqual(state_path.read_bytes(), before_state)
        self.assertEqual(events_path.read_bytes(), before_events)

    def test_existing_pointer_for_other_brain_leaves_no_target_directory(self) -> None:
        other_target = self.sandbox / "must-not-be-created"
        self.assertFalse(other_target.exists())

        payload, _ = self.run_brainctl(
            "init",
            "--brain-root",
            str(other_target),
            "--owner",
            "Other Synthetic Owner",
            "--locale",
            "en",
            "--pointer-file",
            str(self.pointer),
            "--preview-token",
            "not-used-for-existing-pointer",
            "--approval-note",
            "Synthetic rejected target.",
            expected_returncode=2,
        )

        self.assertFalse(payload["ok"])
        self.assertIn("another brain pointer", payload["error"])
        self.assertFalse(other_target.exists())
        history, _ = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(history["clean"])

    def test_scope_inventory_skips_secret_file_and_symlink_target(self) -> None:
        source = self.sandbox / "approved-source"
        outside = self.sandbox / "outside-source"
        source.mkdir()
        outside.mkdir()
        (source / "visible.txt").write_text("safe metadata target", encoding="utf-8")
        secret = source / ".env.production"
        secret.write_text("SYNTHETIC_SECRET_MUST_NOT_APPEAR", encoding="utf-8")
        secret.chmod(0)
        (outside / "never-traverse.txt").write_text(
            "SYNTHETIC_OUTSIDE_CONTENT",
            encoding="utf-8",
        )
        link = source / "escape"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")

        self.approve("metadata-map", source)
        self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(source),
            "--mode",
            "names-only",
            "--label",
            "synthetic-source",
        )
        self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--stage",
            "METADATA_MAP",
            "--next-action",
            "Create the synthetic names-only map.",
        )
        try:
            payload, _ = self.run_brainctl(
                "inventory",
                "--brain-root",
                str(self.brain),
                "--roots",
                str(source),
            )
        finally:
            secret.chmod(stat.S_IRUSR | stat.S_IWUSR)

        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["counts"]["sensitive_skipped"], 1)
        self.assertGreaterEqual(payload["counts"]["symlinks_skipped"], 1)

        inventory_path = self.brain / "work" / "setup" / "inventory.jsonl"
        entries = [
            json.loads(line)
            for line in inventory_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        paths = {entry["path"] for entry in entries}
        self.assertIn("visible.txt", paths)
        self.assertNotIn(".env.production", paths)
        self.assertNotIn("escape", paths)
        self.assertFalse(any("never-traverse.txt" in path for path in paths))

        generated = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                inventory_path,
                self.brain / "work" / "setup" / "inventory-summary.md",
                self.brain / "work" / "setup" / "source-roots.json",
            )
        )
        self.assertNotIn("SYNTHETIC_SECRET_MUST_NOT_APPEAR", generated)
        self.assertNotIn("SYNTHETIC_OUTSIDE_CONTENT", generated)

    def test_inventory_source_aliases_remain_stable_when_roots_reorder(self) -> None:
        source_a = self.sandbox / "stable-source-a"
        source_b = self.sandbox / "stable-source-b"
        source_a.mkdir()
        source_b.mkdir()
        for source in (source_a, source_b):
            self.approve("metadata-map", source)
            self.run_brainctl(
                "scope",
                "--brain-root",
                str(self.brain),
                "--path",
                str(source),
                "--mode",
                "metadata",
            )
        self.set_stage_for_test("METADATA_MAP")
        first, _ = self.run_brainctl(
            "inventory",
            "--brain-root",
            str(self.brain),
            "--roots",
            str(source_a),
            str(source_b),
        )
        self.assertEqual(
            first["roots"],
            {
                "root-1": str(source_a.resolve()),
                "root-2": str(source_b.resolve()),
            },
        )

        self.set_stage_for_test("METADATA_MAP")
        reordered, _ = self.run_brainctl(
            "inventory",
            "--brain-root",
            str(self.brain),
            "--roots",
            str(source_b),
            str(source_a),
        )
        self.assertEqual(
            reordered["roots"],
            {
                "root-1": str(source_a.resolve()),
                "root-2": str(source_b.resolve()),
            },
        )
        registry = json.loads(
            (
                self.brain / "work" / "setup" / "source-roots.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(registry, first["roots"])

    def test_record_consent_area_and_check_with_content_scope_gate(self) -> None:
        source = self.sandbox / "recorded-source"
        source.mkdir()

        consent, _ = self.run_brainctl(
            "record",
            "consent",
            "--brain-root",
            str(self.brain),
            "--kind",
            "metadata-map",
            "--decision",
            "approved",
            "--scope",
            str(source),
            "--note",
            "Synthetic metadata approval",
        )
        self.assertTrue(consent["ok"])
        self.assertEqual(consent["consent"]["kind"], "metadata-map")
        self.assertEqual(consent["consent"]["decision"], "approved")
        self.assertEqual(consent["consent"]["scope"], [str(source.resolve())])

        self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(source),
            "--mode",
            "names-only",
            "--label",
            "recorded-source",
        )
        self.set_stage_for_test("SCOPE_REVIEW")
        area, _ = self.run_brainctl(
            "record",
            "area",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-area",
            "--label",
            "Synthetic area",
            "--paths",
            str(source),
            "--status",
            "names-only",
            "--cursor",
            "cursor-001",
            "--cards",
            "projects/synthetic-area",
            "rules/synthetic-rule",
            "--note",
            "Names only",
        )
        self.assertTrue(area["ok"])
        self.assertEqual(area["area"]["paths"], [str(source.resolve())])
        self.assertEqual(area["area"]["status"], "names-only")
        self.assertEqual(
            area["area"]["cards"],
            ["projects/synthetic-area", "rules/synthetic-rule"],
        )

        self.set_stage_for_test("CONTOUR_LOOP")
        blocked, _ = self.run_brainctl(
            "record",
            "area",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-area",
            "--label",
            "Synthetic area",
            "--paths",
            str(source),
            "--status",
            "content-reviewed",
            expected_returncode=2,
        )
        self.assertFalse(blocked["ok"])
        self.assertIn("not approved", blocked["error"])

        self.set_stage_for_test("ACCEPTANCE_TESTING")
        check, _ = self.run_brainctl(
            "record",
            "check",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-retrieval",
            "--status",
            "pass",
            "--evidence",
            "Synthetic fixture returned its active card.",
        )
        self.assertTrue(check["ok"])
        self.assertEqual(check["check"]["id"], "synthetic-retrieval")
        self.assertEqual(check["check"]["status"], "pass")

        state = json.loads(
            (self.brain / "work" / "setup" / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(state["consents"]), 2)
        self.assertEqual(state["consents"][0]["kind"], "memory-location")
        self.assertEqual(state["consents"][1]["note"], "Synthetic metadata approval")
        self.assertEqual(state["areas"]["synthetic-area"]["coverage"], "names-only")
        self.assertEqual(state["areas"]["synthetic-area"]["execution"], "closed")
        self.assertEqual(
            state["checks"]["synthetic-retrieval"]["evidence"],
            "Synthetic fixture returned its active card.",
        )

        self.set_stage_for_test("DEEP_AUDIT_GATE")
        self.approve("content-audit", source)
        self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(source),
            "--mode",
            "content",
            "--label",
            "recorded-source",
        )
        self.set_stage_for_test("CONTOUR_LOOP")
        self.run_brainctl(
            "snapshot",
            "create",
            "--brain-root",
            str(self.brain),
            "--root",
            str(source),
            "--label",
            "Synthetic area",
        )
        journal = self.brain / "journal" / "2026-07-23-synthetic-area.md"
        journal.write_text(
            """---
title: "Synthetic area review"
type: journal
created: 2026-07-23
origin: observation
summary: "Synthetic source review completed"
---

# Synthetic area review

No durable source facts were found in this empty synthetic area.
""",
            encoding="utf-8",
        )
        saved, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "journal/2026-07-23-synthetic-area.md",
            "--summary",
            "record synthetic area review",
            "--reason",
            "exercise complete contour closure requirements",
        )
        self.run_brainctl(
            "snapshot",
            "verify",
            "--brain-root",
            str(self.brain),
            "--root",
            str(source),
            "--label",
            "Synthetic area",
        )
        unrelated = self.sandbox / "unrelated-source"
        unrelated.mkdir()
        self.approve("content-audit", unrelated)
        self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(unrelated),
            "--mode",
            "content",
        )
        mismatched, _ = self.run_brainctl(
            "record",
            "area",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-area",
            "--label",
            "Synthetic area",
            "--paths",
            str(source),
            str(unrelated),
            "--status",
            "content-reviewed",
            "--journal",
            "journal/2026-07-23-synthetic-area.md",
            "--saved-revision",
            saved["revision"],
            expected_returncode=2,
        )
        self.assertIn("exactly match", mismatched["error"])
        reviewed, _ = self.run_brainctl(
            "record",
            "area",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-area",
            "--label",
            "Synthetic area",
            "--paths",
            str(source),
            "--status",
            "content-reviewed",
            "--cards",
            "projects/synthetic-area",
            "--journal",
            "journal/2026-07-23-synthetic-area.md",
            "--saved-revision",
            saved["revision"],
        )
        self.assertTrue(reviewed["ok"])
        self.assertEqual(reviewed["area"]["status"], "content-reviewed")

    def test_snapshot_verify_detects_source_mutation(self) -> None:
        source = self.sandbox / "content-source"
        source.mkdir()
        self.set_stage_for_test("CONTOUR_LOOP")
        tracked = source / "tracked.txt"
        tracked.write_text("before", encoding="utf-8")
        unreadable = source / "private-value.txt"
        unreadable.write_text(
            "password=synthetic-secret-that-must-not-be-opened",
            encoding="utf-8",
        )
        unreadable.chmod(0)
        self.addCleanup(lambda: unreadable.chmod(0o600) if unreadable.exists() else None)
        self.approve("content-audit", source)
        self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(source),
            "--mode",
            "content",
        )
        created, _ = self.run_brainctl(
            "snapshot",
            "create",
            "--brain-root",
            str(self.brain),
            "--root",
            str(source),
            "--label",
            "synthetic-contour",
        )
        self.assertTrue(created["ok"])
        snapshot = json.loads(Path(created["snapshot"]).read_text(encoding="utf-8"))
        file_entries = [
            entry for entry in snapshot["entries"] if entry["kind"] == "file"
        ]
        self.assertTrue(any(entry["path"] == "private-value.txt" for entry in file_entries))
        self.assertTrue(
            all("sha256" not in entry and "content_hash" not in entry for entry in file_entries)
        )

        tracked.write_text("after mutation with a different size", encoding="utf-8")
        verified, _ = self.run_brainctl(
            "snapshot",
            "verify",
            "--brain-root",
            str(self.brain),
            "--root",
            str(source),
            "--label",
            "synthetic-contour",
            expected_returncode=1,
        )
        self.assertFalse(verified["ok"])
        self.assertFalse(verified["diff"]["pass"])
        self.assertIn("tracked.txt", verified["diff"]["changed"])
        state = json.loads(
            (self.brain / "work" / "setup" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["areas"]["synthetic-contour"]["coverage"], "needs-recheck")
        self.assertEqual(state["areas"]["synthetic-contour"]["execution"], "in-progress")
        self.assertEqual(state["substate"], "CONTOUR_PRECHECK")

    def test_pause_can_resume_only_from_recorded_state(self) -> None:
        source = self.sandbox / "pause-source"
        source.mkdir()
        self.approve("metadata-map", source)
        self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(source),
            "--mode",
            "names-only",
        )
        advanced, _ = self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--stage",
            "METADATA_MAP",
            "--next-action",
            "Map the synthetic source.",
        )
        self.assertEqual(advanced["to"], "METADATA_MAP")

        paused, _ = self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--stage",
            "PAUSED",
            "--next-action",
            "Resume the synthetic map.",
        )
        self.assertEqual(paused["from"], "METADATA_MAP")
        self.assertEqual(paused["to"], "PAUSED")

        rejected, _ = self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--stage",
            "SCOPE_REVIEW",
            expected_returncode=2,
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("recorded state 'METADATA_MAP'", rejected["error"])
        state_after_rejection, _ = self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--show",
        )
        self.assertEqual(state_after_rejection["state"]["state"], "PAUSED")
        self.assertEqual(
            state_after_rejection["state"]["resume_from"],
            "METADATA_MAP",
        )

        resumed, _ = self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--stage",
            "METADATA_MAP",
        )
        self.assertEqual(resumed["from"], "PAUSED")
        self.assertEqual(resumed["to"], "METADATA_MAP")

    def test_bounded_connect_preview_apply_and_remove(self) -> None:
        target = self.sandbox / "agent-instructions.md"
        original = (
            "# Existing instructions\n\n"
            "Keep this user-owned line.  \n"
            "The user's unrelated note says prefix-newline=1.\n\n"
        )
        target.write_text(original, encoding="utf-8")
        self.set_stage_for_test("CONNECTION_REVIEW")
        self.approve("connection-read", target)
        self.approve("connection", target)
        self.run_brainctl(
            "record",
            "connection",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            "--status",
            "pending-approval",
        )

        preview, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
        )
        self.assertTrue(preview["ok"])
        self.assertIn("START PERSONAL BRAIN", preview["diff"])
        self.assertEqual(target.read_text(encoding="utf-8"), original)

        advanced, _ = self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--stage",
            "CONNECTION_APPLY",
            "--next-action",
            "Apply the exact reviewed connection.",
        )
        self.assertEqual(advanced["to"], "CONNECTION_APPLY")

        applied, _ = self.run_brainctl(
            "connect",
            "apply",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            "--token",
            preview["confirmation_token"],
        )
        self.assertEqual(applied["action"], "connected")
        connected = target.read_text(encoding="utf-8")
        prefix_was_preserved = connected.startswith(original)
        self.assertEqual(connected.count("<!-- START PERSONAL BRAIN -->"), 1)
        self.assertEqual(connected.count("<!-- END PERSONAL BRAIN -->"), 1)
        self.assertIn(str(self.brain), connected)

        idempotent_preview, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
        )
        self.assertEqual(idempotent_preview["diff"], "")

        removal_preview, _ = self.run_brainctl(
            "connect",
            "remove-preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
        )
        self.assertIn("START PERSONAL BRAIN", removal_preview["diff"])
        removed, _ = self.run_brainctl(
            "connect",
            "remove",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            "--token",
            removal_preview["confirmation_token"],
        )
        self.assertEqual(removed["action"], "removed")
        state = json.loads(
            (self.brain / "work" / "setup" / "state.json").read_text(encoding="utf-8")
        )
        self.assertNotIn(str(target), state["connections"])
        with self.subTest("apply preserves all user-owned prefix bytes"):
            self.assertTrue(prefix_was_preserved)
        with self.subTest("remove restores all original bytes"):
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_connect_requires_exact_consent_and_the_reviewed_stage(self) -> None:
        target = self.sandbox / "exact-consent-target.md"
        original = b"# User instructions\n"
        target.write_bytes(original)
        self.set_stage_for_test("CONNECTION_REVIEW")
        self.select_connection(target)

        missing, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            expected_returncode=2,
        )
        self.assertIn("exact active connection-read consent", missing["error"].casefold())

        self.approve("connection-read", target.parent)
        parent_only, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            expected_returncode=2,
        )
        self.assertIn(
            "exact active connection-read consent",
            parent_only["error"].casefold(),
        )

        self.approve("connection-read", target)
        self.approve("connection", target)
        self.set_stage_for_test("METADATA_MAP")
        wrong_preview_stage, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            expected_returncode=2,
        )
        self.assertIn("only during connection review", wrong_preview_stage["error"])

        self.set_stage_for_test("CONNECTION_REVIEW")
        preview, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
        )
        wrong_apply_stage, _ = self.run_brainctl(
            "connect",
            "apply",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            "--token",
            preview["confirmation_token"],
            expected_returncode=2,
        )
        self.assertIn("only after the reviewed target", wrong_apply_stage["error"])
        self.assertEqual(target.read_bytes(), original)

        self.set_stage_for_test("CONNECTION_APPLY")
        applied, _ = self.run_brainctl(
            "connect",
            "apply",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            "--token",
            preview["confirmation_token"],
        )
        self.assertEqual(applied["action"], "connected")

    def test_verified_connection_requires_fresh_conversation_evidence(self) -> None:
        self.set_stage_for_test("CONNECTION_REVIEW")
        target = self.sandbox / "evidence-target.md"
        target.write_text("# Synthetic assistant instructions\n", encoding="utf-8")
        self.select_connection(target)

        blocked, _ = self.run_brainctl(
            "record",
            "connection",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            "--status",
            "verified",
            expected_returncode=2,
        )
        self.assertIn("fresh conversation", blocked["error"].casefold())

    def test_connect_remove_round_trip_preserves_line_endings_and_final_newline(self) -> None:
        samples = {
            "crlf": b"# Existing\r\n\r\nKeep this line.  \r\n",
            "no-final-newline": b"# Existing\n\nKeep this final line.",
        }
        for label, original in samples.items():
            with self.subTest(label=label):
                target = self.sandbox / f"{label}-instructions.md"
                target.write_bytes(original)
                self.set_stage_for_test("CONNECTION_REVIEW")
                self.approve("connection-read", target)
                self.approve("connection", target)
                self.select_connection(target)

                preview, _ = self.run_brainctl(
                    "connect",
                    "preview",
                    "--brain-root",
                    str(self.brain),
                    "--target",
                    str(target),
                )
                self.set_stage_for_test("CONNECTION_APPLY")
                self.run_brainctl(
                    "connect",
                    "apply",
                    "--brain-root",
                    str(self.brain),
                    "--target",
                    str(target),
                    "--token",
                    preview["confirmation_token"],
                )
                connected = target.read_bytes()
                self.assertIn(b"<!-- START PERSONAL BRAIN -->", connected)
                if label == "crlf":
                    self.assertNotIn(b"\n", connected.replace(b"\r\n", b""))

                removal_preview, _ = self.run_brainctl(
                    "connect",
                    "remove-preview",
                    "--brain-root",
                    str(self.brain),
                    "--target",
                    str(target),
                )
                self.run_brainctl(
                    "connect",
                    "remove",
                    "--brain-root",
                    str(self.brain),
                    "--target",
                    str(target),
                    "--token",
                    removal_preview["confirmation_token"],
                )
                self.assertEqual(target.read_bytes(), original)

    def test_connect_refuses_to_edit_symlink(self) -> None:
        self.set_stage_for_test("CONNECTION_REVIEW")
        real_target = self.sandbox / "real-agent-instructions.md"
        real_content = "# Synthetic host instructions\n"
        real_target.write_text(real_content, encoding="utf-8")
        symlink_target = self.sandbox / "linked-agent-instructions.md"
        try:
            os.symlink(real_target, symlink_target)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"file symlinks are unavailable: {exc}")

        payload, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(symlink_target),
            expected_returncode=2,
        )
        self.assertFalse(payload["ok"])
        self.assertIn("symlink", payload["error"].casefold())
        self.assertTrue(symlink_target.is_symlink())
        self.assertEqual(real_target.read_text(encoding="utf-8"), real_content)
        backup_dir = self.brain / "work" / "setup" / "backups"
        self.assertEqual(list(backup_dir.iterdir()), [])

    def test_connect_refuses_to_edit_hard_link(self) -> None:
        self.set_stage_for_test("CONNECTION_REVIEW")
        original = self.sandbox / "original-instructions.md"
        hard_link = self.sandbox / "hard-linked-instructions.md"
        original_bytes = b"# Shared inode must remain untouched\n"
        original.write_bytes(original_bytes)
        try:
            os.link(original, hard_link)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"hard links are unavailable: {exc}")
        self.approve("connection-read", hard_link)
        self.select_connection(hard_link)

        payload, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(hard_link),
            expected_returncode=2,
        )
        self.assertIn("hard-linked", payload["error"].casefold())
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(hard_link.read_bytes(), original_bytes)
        backup_dir = self.brain / "work" / "setup" / "backups"
        self.assertEqual(list(backup_dir.iterdir()), [])

    def test_connect_refuses_secret_bearing_target_before_backup(self) -> None:
        self.set_stage_for_test("CONNECTION_REVIEW")
        target = self.sandbox / "secret-bearing-instructions.md"
        original = b"# Instructions\n\npassword=syntheticvalue123456\n"
        target.write_bytes(original)
        self.approve("connection-read", target)
        self.select_connection(target)

        blocked, _ = self.run_brainctl(
            "connect",
            "preview",
            "--brain-root",
            str(self.brain),
            "--target",
            str(target),
            expected_returncode=2,
        )
        self.assertIn("secret-bearing", blocked["error"].casefold())
        self.assertEqual(target.read_bytes(), original)
        backup_dir = self.brain / "work" / "setup" / "backups"
        self.assertEqual(list(backup_dir.iterdir()), [])

    def test_save_records_one_valid_exact_path(self) -> None:
        card = self.brain / "rules" / "concise-output.md"
        card.write_text(
            """---
title: "Concise output"
type: rule
created: 2026-01-15
origin: stated
stated_in: 2026-01-15
summary: "Prefer concise output"
---

# Concise output

Keep routine output concise.
""",
            encoding="utf-8",
        )
        before = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
            expected_returncode=1,
        )[0]["head"]
        payload, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "rules/concise-output.md",
            "--summary",
            "add concise output rule",
            "--reason",
            "synthetic owner explicitly approved it",
        )
        after = payload["revision"]
        self.assertTrue(payload["ok"])
        self.assertNotEqual(before, after)
        self.assertEqual(payload["paths"], ["rules/concise-output.md"])
        history, _ = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(history["clean"])
        self.assertEqual(history["head"], after)

    def test_search_refuses_valid_but_unsaved_memory(self) -> None:
        card = self.brain / "rules" / "unsaved-search-result.md"
        card.write_bytes(
            self.valid_rule_bytes(
                "Unsaved search result",
                "A valid unsaved card must stay unavailable",
                "This card must not appear until its exact change is saved.",
            )
        )
        blocked, _ = self.run_brainctl(
            "search",
            "--brain-root",
            str(self.brain),
            "--query",
            "unsaved search result",
            expected_returncode=2,
        )
        self.assertIn("unsaved", blocked["error"].casefold())

    def test_validation_failure_blocks_history_save(self) -> None:
        invalid = self.brain / "rules" / "invalid-rule.md"
        invalid.write_text(
            """---
title: "Invalid rule"
type: rule
created: 2026-01-15
origin: stated
---

# Invalid rule

This synthetic card deliberately omits stated provenance.
""",
            encoding="utf-8",
        )
        before = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
            expected_returncode=1,
        )[0]["head"]
        validation, _ = self.run_brainctl(
            "validate",
            "--brain-root",
            str(self.brain),
            expected_returncode=1,
        )
        self.assertFalse(validation["ok"])
        self.assertTrue(
            any("stated card requires stated_in" in error for error in validation["errors"])
        )

        save, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "rules/invalid-rule.md",
            "--summary",
            "attempt invalid rule",
            "--reason",
            "exercise validation gate",
            expected_returncode=2,
        )
        self.assertFalse(save["ok"])
        self.assertIn("validation failed", save["error"].casefold())
        after = self.run_brainctl(
            "history",
            "list",
            "--brain-root",
            str(self.brain),
        )[0]["revisions"][0]["id"]
        self.assertEqual(before, after)

    def test_consent_is_canonical_and_cannot_follow_retargeted_symlink(self) -> None:
        source_a = self.sandbox / "source-a"
        source_b = self.sandbox / "source-b"
        source_a.mkdir()
        source_b.mkdir()
        linked = self.sandbox / "selected-source"
        try:
            linked.symlink_to(source_a, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")

        consent, _ = self.run_brainctl(
            "record",
            "consent",
            "--brain-root",
            str(self.brain),
            "--kind",
            "metadata-map",
            "--decision",
            "approved",
            "--scope",
            str(linked),
            "--note",
            "Approve only the first resolved synthetic source.",
        )
        self.assertEqual(consent["consent"]["scope"], [str(source_a.resolve())])

        linked.unlink()
        linked.symlink_to(source_b, target_is_directory=True)
        blocked, _ = self.run_brainctl(
            "scope",
            "--brain-root",
            str(self.brain),
            "--path",
            str(linked),
            "--mode",
            "metadata",
            expected_returncode=2,
        )
        self.assertIn(
            "exact active metadata-map consent",
            blocked["error"].casefold(),
        )

    def test_setup_symlink_is_rejected_before_outside_state_write(self) -> None:
        setup = self.brain / "work" / "setup"
        outside = self.sandbox / "outside-setup"
        setup.rename(outside)
        before = (outside / "state.json").read_bytes()
        try:
            setup.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")

        blocked, _ = self.run_brainctl(
            "status",
            "--brain-root",
            str(self.brain),
            expected_returncode=2,
        )
        self.assertIn("real directory", blocked["error"])
        self.assertEqual((outside / "state.json").read_bytes(), before)

    def test_secret_in_card_body_and_non_markdown_knowledge_are_rejected(self) -> None:
        secret_card = self.brain / "rules" / "blocked-secret.md"
        secret_card.write_bytes(
            self.valid_rule_bytes(
                "Blocked secret",
                "A synthetic credential must not be saved",
                "password=syntheticvalue12345",
            )
        )
        blocked, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "rules/blocked-secret.md",
            "--summary",
            "attempt secret save",
            "--reason",
            "exercise complete-card secret scanning",
            expected_returncode=2,
        )
        self.assertIn("secret-like value", blocked["error"])

        secret_card.unlink()
        binary = self.brain / "projects" / "unsupported.bin"
        binary.write_bytes(b"\x00synthetic")
        blocked_binary, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "projects/unsupported.bin",
            "--summary",
            "attempt binary save",
            "--reason",
            "exercise Markdown-only canonical knowledge",
            expected_returncode=2,
        )
        self.assertIn("non-Markdown", blocked_binary["error"])

    def test_core_profile_placeholders_block_confirmation(self) -> None:
        self.set_stage_for_test("CORE_REVIEW")
        blocked, _ = self.run_brainctl(
            "record",
            "core",
            "--brain-root",
            str(self.brain),
            "--note",
            "Synthetic owner reviewed the profile.",
            expected_returncode=2,
        )
        self.assertIn("onboarding placeholders", blocked["error"])

    def test_session_metadata_and_content_permissions_are_separate(self) -> None:
        session_source = self.sandbox / "session-export"
        session_source.mkdir()
        self.set_stage_for_test("SESSIONS_PERMISSION")
        blocked_metadata, _ = self.run_brainctl(
            "record",
            "session",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-history",
            "--program",
            "Synthetic assistant",
            "--path",
            str(session_source),
            "--execution",
            "pending",
            "--coverage",
            "metadata-only",
            expected_returncode=2,
        )
        self.assertIn(
            "session-history-metadata",
            blocked_metadata["error"],
        )
        self.approve("session-history-metadata", session_source)
        recorded, _ = self.run_brainctl(
            "record",
            "session",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-history",
            "--program",
            "Synthetic assistant",
            "--path",
            str(session_source),
            "--execution",
            "pending",
            "--coverage",
            "metadata-only",
        )
        self.assertEqual(
            recorded["session_source"]["coverage"],
            "metadata-only",
        )

        self.set_stage_for_test("SESSION_HISTORY_AUDIT")
        blocked_content, _ = self.run_brainctl(
            "record",
            "session",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-history",
            "--program",
            "Synthetic assistant",
            "--path",
            str(session_source),
            "--execution",
            "closed",
            "--coverage",
            "transcripts-reviewed",
            expected_returncode=2,
        )
        self.assertIn("session-history consent", blocked_content["error"])
        self.approve("session-history", session_source)
        reviewed, _ = self.run_brainctl(
            "record",
            "session",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-history",
            "--program",
            "Synthetic assistant",
            "--path",
            str(session_source),
            "--execution",
            "closed",
            "--coverage",
            "transcripts-reviewed",
        )
        self.assertEqual(
            reviewed["session_source"]["coverage"],
            "transcripts-reviewed",
        )

        other_source = self.sandbox / "other-session-export"
        other_source.mkdir()
        self.approve("session-history", other_source)
        rebound, _ = self.run_brainctl(
            "record",
            "session",
            "--brain-root",
            str(self.brain),
            "--id",
            "synthetic-history",
            "--program",
            "Synthetic assistant",
            "--path",
            str(other_source),
            "--execution",
            "closed",
            "--coverage",
            "transcripts-reviewed",
            expected_returncode=2,
        )
        self.assertIn("cannot be rebound", rebound["error"])

    def test_dirty_memory_cannot_transition_from_handoff_to_ready(self) -> None:
        profile = self.brain / "profile" / "owner.md"
        profile_text = profile.read_text(encoding="utf-8")
        profile_text = profile_text.replace(
            "To be completed during onboarding.",
            "The owner builds and reviews synthetic software systems.",
        ).replace(
            "Other confirmed preferences will be added during onboarding.",
            "The owner confirmed concise explanations and explicit safety boundaries.",
        ).replace(
            "Safety choices will be added during onboarding.",
            "Never open synthetic credentials; never store synthetic private drafts.",
        )
        profile.write_text(profile_text, encoding="utf-8")
        self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "profile/owner.md",
            "--summary",
            "complete synthetic owner profile",
            "--reason",
            "prepare the confirmed compact core",
        )
        self.set_stage_for_test("CORE_REVIEW")
        self.run_brainctl(
            "record",
            "core",
            "--brain-root",
            str(self.brain),
            "--note",
            "Synthetic owner confirmed language and compact profile.",
        )
        self.set_stage_for_test("HANDOFF")
        state_path = self.brain / "work" / "setup" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        required = (
            "library-structure",
            "no-secret-values",
            "source-integrity",
            "active-version-routing",
            "origin-priority",
            "freshness-warning",
            "excluded-scope-blocked",
            "fresh-session-general",
            "fresh-session-project",
            "remember-replace-cleanup",
            "pause-resume",
        )
        state["checks"] = {
            check_id: {
                "status": "pass",
                "evidence": "Synthetic release check passed.",
                "checked_at": "2026-07-23T10:00:00+00:00",
            }
            for check_id in required
        }
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        dirty = self.brain / "rules" / "unsaved-ready-blocker.md"
        dirty.write_bytes(
            self.valid_rule_bytes(
                "Unsaved ready blocker",
                "Unsaved changes must block ready",
                "This valid but unsaved card must keep the setup from becoming ready.",
            )
        )

        blocked, _ = self.run_brainctl(
            "state",
            "--brain-root",
            str(self.brain),
            "--stage",
            "READY",
            expected_returncode=2,
        )
        self.assertIn("must be saved", blocked["error"])

    def test_forged_connection_recovery_requires_exact_consent(self) -> None:
        target = self.sandbox / "forged-recovery-target.md"
        original = b"# Original instructions\n"
        after = b"# Unapproved replacement\n"
        target.write_bytes(after)
        self.set_stage_for_test("CONNECTION_REVIEW")
        self.select_connection(target)
        state = json.loads(
            (self.brain / "work" / "setup" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        backup = self.brain / "work" / "setup" / "backups" / "forged.bak"
        backup.write_bytes(original)
        transaction = {
            "schema_version": 1,
            "status": "pending",
            "created_at": "2026-07-23T10:00:00+00:00",
            "brain_id": state["brain_id"],
            "action": "connect",
            "target": str(target.resolve()),
            "before_exists": True,
            "before_sha256": hashlib.sha256(original).hexdigest(),
            "after_sha256": hashlib.sha256(after).hexdigest(),
            "backup": str(backup.resolve()),
            "connection_state_before": state["connections"][str(target.resolve())],
        }
        transaction_path = (
            self.brain
            / "work"
            / "setup"
            / "transactions"
            / "connect-forged.json"
        )
        transaction_path.write_text(
            json.dumps(transaction, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        transaction_path.chmod(0o600)

        blocked, _ = self.run_brainctl(
            "connect",
            "recover-preview",
            "--brain-root",
            str(self.brain),
            expected_returncode=2,
        )
        self.assertIn("exact active connection consent", blocked["error"].casefold())
        self.assertEqual(target.read_bytes(), after)

    def test_portable_history_cli_save_list_preview_and_undo(self) -> None:
        card = self.brain / "rules" / "history-round-trip.md"
        first_bytes = self.valid_rule_bytes(
            "History round trip",
            "First approved version",
            "This is the first approved body.",
        )
        second_bytes = self.valid_rule_bytes(
            "History round trip",
            "Second approved version",
            "This is the second approved body.",
        )

        card.write_bytes(first_bytes)
        first_save, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "rules/history-round-trip.md",
            "--summary",
            "save first approved version",
            "--reason",
            "create a stable undo target",
        )
        first_revision = first_save["revision"]

        card.write_bytes(second_bytes)
        second_save, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "rules/history-round-trip.md",
            "--summary",
            "save second approved version",
            "--reason",
            "exercise a later revision",
        )
        second_revision = second_save["revision"]

        listed, _ = self.run_brainctl(
            "history",
            "list",
            "--brain-root",
            str(self.brain),
        )
        listed_ids = [item["id"] for item in listed["revisions"]]
        self.assertEqual(listed_ids[:2], [second_revision, first_revision])

        preview, _ = self.run_brainctl(
            "history",
            "undo-preview",
            "--brain-root",
            str(self.brain),
            "--to-revision",
            first_revision,
        )
        self.assertEqual(preview["from_revision"], second_revision)
        self.assertEqual(preview["to_revision"], first_revision)
        self.assertEqual(
            preview["changes"]["modified"],
            ["rules/history-round-trip.md"],
        )
        self.assertEqual(card.read_bytes(), second_bytes)

        restored, _ = self.run_brainctl(
            "history",
            "undo",
            "--brain-root",
            str(self.brain),
            "--to-revision",
            first_revision,
            "--token",
            preview["token"],
            "--reason",
            "restore the first approved version",
        )
        self.assertEqual(restored["restored_from"], first_revision)
        self.assertNotIn(
            restored["revision"],
            {first_revision, second_revision},
        )
        self.assertEqual(card.read_bytes(), first_bytes)

        status, _ = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(status["clean"])
        self.assertTrue(status["revision_matches_state"])
        after_undo, _ = self.run_brainctl(
            "history",
            "list",
            "--brain-root",
            str(self.brain),
        )
        self.assertEqual(after_undo["revisions"][0]["kind"], "undo")

    def test_cli_recovers_sigkill_after_published_undo_and_stale_locks(
        self,
    ) -> None:
        card = self.brain / "rules" / "interrupted-history.md"
        first_bytes = self.valid_rule_bytes(
            "Interrupted history",
            "First approved interrupted-history version",
            "This is the first approved interrupted-history body.",
        )
        second_bytes = self.valid_rule_bytes(
            "Interrupted history",
            "Second approved interrupted-history version",
            "This is the second approved interrupted-history body.",
        )
        card.write_bytes(first_bytes)
        first, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "rules/interrupted-history.md",
            "--summary",
            "save first interrupted-history version",
            "--reason",
            "prepare a recovery target",
        )
        card.write_bytes(second_bytes)
        second, _ = self.run_brainctl(
            "save",
            "--brain-root",
            str(self.brain),
            "--paths",
            "rules/interrupted-history.md",
            "--summary",
            "save second interrupted-history version",
            "--reason",
            "prepare an interrupted undo",
        )
        preview, _ = self.run_brainctl(
            "history",
            "undo-preview",
            "--brain-root",
            str(self.brain),
            "--to-revision",
            first["revision"],
        )

        with mock.patch.object(
            portable_history.shutil,
            "rmtree",
            side_effect=SimulatedKill("simulated SIGKILL after HEAD publication"),
        ):
            with self.assertRaises(SimulatedKill):
                portable_history.undo_apply(
                    self.brain,
                    first["revision"],
                    preview["token"],
                    "publish the undo and interrupt cleanup",
                )
        published_head = (
            self.brain / ".brain-history" / "HEAD"
        ).read_text(encoding="ascii").strip()
        self.assertNotIn(
            published_head,
            {first["revision"], second["revision"]},
        )

        history_lock = self.brain / ".brain-history" / "LOCK"
        history_lock.write_bytes(
            portable_history._canonical_bytes(
                {
                    "version": portable_history.FORMAT_VERSION,
                    "pid": 999_999_999,
                    "host": platform.node() or "unknown-local-host",
                    "created_at": portable_history._now(),
                    "nonce": "a" * 32,
                }
            )
            + b"\n"
        )
        history_lock.chmod(0o600)

        operation_lock = self.brain / "work" / "setup" / "operation.lock"
        operation_lock.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "token": "b" * 32,
                    "pid": 999_999_999,
                    "host": platform.node(),
                    "purpose": "history:undo",
                    "created_at": portable_history._now(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        operation_lock.chmod(0o600)

        recovery_preview, _ = self.run_brainctl(
            "history",
            "recover-preview",
            "--brain-root",
            str(self.brain),
        )
        self.assertEqual(
            recovery_preview["action"],
            "cleanup-published-undo",
        )
        blocked, _ = self.run_brainctl(
            "history",
            "recover",
            "--brain-root",
            str(self.brain),
            "--token",
            recovery_preview["token"],
            expected_returncode=2,
        )
        self.assertIn("interrupted lock", blocked["error"].casefold())
        self.assertTrue(history_lock.exists())

        lock_preview, _ = self.run_brainctl(
            "lock",
            "clear-preview",
            "--brain-root",
            str(self.brain),
        )
        self.run_brainctl(
            "lock",
            "clear",
            "--brain-root",
            str(self.brain),
            "--token",
            lock_preview["clear_token"],
        )
        recovery_preview, _ = self.run_brainctl(
            "history",
            "recover-preview",
            "--brain-root",
            str(self.brain),
        )
        recovered, _ = self.run_brainctl(
            "history",
            "recover",
            "--brain-root",
            str(self.brain),
            "--token",
            recovery_preview["token"],
        )
        self.assertEqual(recovered["action"], "cleanup-published-undo")
        self.assertEqual(recovered["head"], published_head)
        self.assertEqual(card.read_bytes(), first_bytes)
        self.assertFalse(history_lock.exists())
        self.assertFalse(operation_lock.exists())
        self.assertEqual(
            list((self.brain / ".brain-history" / "transactions").iterdir()),
            [],
        )

        state = json.loads(
            (self.brain / "work" / "setup" / "state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["expected_revision"], published_head)
        self.assertEqual(state["state"], "FAILED_CLOSED")
        status, _ = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(status["clean"])
        self.assertTrue(status["revision_matches_state"])

    def test_cli_reconciles_one_exact_history_revision_after_state_save_kill(
        self,
    ) -> None:
        state_path = self.brain / "work" / "setup" / "state.json"
        before_state = json.loads(state_path.read_text(encoding="utf-8"))
        card = self.brain / "rules" / "reconcile-history.md"
        card.write_bytes(
            self.valid_rule_bytes(
                "Reconcile history",
                "A direct save awaiting state reconciliation",
                "The history revision is complete but its state update was interrupted.",
            )
        )
        published_head = portable_history.save(
            self.brain,
            expected_paths=["rules/reconcile-history.md"],
            summary="publish one revision before state persistence",
            reason="simulate interruption between history and state writes",
        )
        self.assertNotEqual(
            before_state["expected_revision"],
            published_head,
        )

        preview, _ = self.run_brainctl(
            "history",
            "reconcile-preview",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(preview["needed"])
        self.assertEqual(preview["kind"], "save")
        self.assertEqual(preview["to_revision"], published_head)
        blocked, _ = self.run_brainctl(
            "history",
            "reconcile",
            "--brain-root",
            str(self.brain),
            "--token",
            "0" * 64,
            expected_returncode=2,
        )
        self.assertIn("token", blocked["error"].casefold())
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8"))[
                "expected_revision"
            ],
            before_state["expected_revision"],
        )

        reconciled, _ = self.run_brainctl(
            "history",
            "reconcile",
            "--brain-root",
            str(self.brain),
            "--token",
            preview["confirmation_token"],
        )
        self.assertTrue(reconciled["reconciled"])
        self.assertEqual(reconciled["to_revision"], published_head)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["expected_revision"], published_head)
        self.assertEqual(state["state"], "FAILED_CLOSED")
        status, _ = self.run_brainctl(
            "history",
            "status",
            "--brain-root",
            str(self.brain),
        )
        self.assertTrue(status["revision_matches_state"])


if __name__ == "__main__":
    unittest.main()
