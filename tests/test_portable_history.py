"""Tests for the dependency-free personal-brain history backend.

The suite deliberately clears PATH while exercising the public API. This
guards the portable backend against accidentally depending on Git or another
external executable.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "skills" / "build-personal-brain" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

import portable_history as history  # noqa: E402


class SimulatedKill(BaseException):
    """Model SIGKILL-like interruption by bypassing ``except Exception``."""


class PortableHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "brain"
        (self.root / "profile").mkdir(parents=True)
        (self.root / "profile" / "owner.md").write_bytes(b"name: Example\n")

        self.path_patch = mock.patch.dict(os.environ, {"PATH": ""})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    @property
    def history_directory(self) -> Path:
        return self.root / ".brain-history"

    def initialize(self) -> str:
        return history.initialize(
            self.root,
            summary="Initialize portable brain history",
            reason="Establish the first verified snapshot",
        )

    def write_history_lock(self, *, pid: int, nonce: str = "0" * 32) -> Path:
        lock = self.history_directory / "LOCK"
        payload = {
            "version": history.FORMAT_VERSION,
            "pid": pid,
            "host": platform.node() or "unknown-local-host",
            "created_at": history._now(),
            "nonce": nonce,
        }
        lock.write_bytes(history._canonical_bytes(payload) + b"\n")
        os.chmod(str(lock), history.PRIVATE_FILE_MODE)
        return lock

    def prepare_undo_fixture(self) -> tuple[str, str, Path, Path]:
        removed = self.root / "removed.bin"
        removed.write_bytes(b"present in the initial state")
        initial_revision = self.initialize()

        owner = self.root / "profile" / "owner.md"
        owner.write_bytes(b"name: Second state\n")
        removed.unlink()
        added = self.root / "added.bin"
        added.write_bytes(b"present in the second state")
        second_revision = history.save(
            self.root,
            expected_paths=["added.bin", "profile/owner.md", "removed.bin"],
            summary="Create the second recovery state",
            reason="Prepare interrupted undo recovery coverage",
        )
        return initial_revision, second_revision, added, removed

    def test_initialize_inspect_save_and_list_without_git(self) -> None:
        initial_revision = self.initialize()
        initial_status = history.inspect(self.root)
        self.assertEqual(
            initial_status,
            {
                "integrity": "ok",
                "head": initial_revision,
                "clean": True,
                "added": [],
                "modified": [],
                "deleted": [],
            },
        )

        self.assertEqual(self.initialize(), initial_revision)
        self.assertEqual(len(history.list_revisions(self.root)), 1)

        (self.root / "profile" / "owner.md").write_bytes(b"name: Example\nlocale: en\n")
        (self.root / "projects").mkdir()
        (self.root / "projects" / "current.md").write_bytes(b"# Current\n")

        # These areas are intentionally outside the portable history.
        for excluded in (
            self.root / ".git" / "config",
            self.root / "work" / "setup" / "scratch.txt",
            self.root / "cache" / "response.json",
            self.root / "backups" / "old.md",
            self.root / "projects" / "embedded" / ".git" / "config",
            self.root / "projects" / "embedded" / "work" / "setup" / "note",
            self.root / "projects" / "embedded" / ".brain-history" / "HEAD",
        ):
            excluded.parent.mkdir(parents=True, exist_ok=True)
            excluded.write_bytes(b"ignored")

        expected = ["profile/owner.md", "projects/current.md"]
        self.assertEqual(history.changed_paths(self.root), expected)
        changed_status = history.inspect(self.root)
        self.assertEqual(changed_status["added"], ["projects/current.md"])
        self.assertEqual(changed_status["modified"], ["profile/owner.md"])
        self.assertEqual(changed_status["deleted"], [])

        saved_revision = history.save(
            self.root,
            expected_paths=reversed(expected),
            summary="Update owner and current project",
            reason="Persist a deliberately scoped change",
        )
        self.assertNotEqual(saved_revision, initial_revision)
        self.assertTrue(history.inspect(self.root)["clean"])

        revisions = history.list_revisions(self.root)
        self.assertEqual(
            [item["id"] for item in revisions],
            [
                saved_revision,
                initial_revision,
            ],
        )
        self.assertEqual(revisions[0]["kind"], "save")
        self.assertEqual(revisions[0]["parent"], initial_revision)
        self.assertEqual(revisions[0]["changed_paths"], expected)
        self.assertEqual(revisions[1]["kind"], "init")

        revision_file = (
            self.history_directory / "revisions" / (saved_revision + ".json")
        )
        parsed_revision = json.loads(revision_file.read_text(encoding="utf-8"))
        self.assertEqual(
            revision_file.read_bytes(),
            history._canonical_bytes(parsed_revision) + b"\n",
        )

    def test_save_requires_an_exact_safe_path_set(self) -> None:
        initial_revision = self.initialize()
        (self.root / "profile" / "owner.md").write_bytes(b"name: Changed\n")
        head_before = (self.history_directory / "HEAD").read_bytes()

        for expected_paths in (
            [],
            ["profile/owner.md", "missing.md"],
            ["profile/owner.md", "profile/owner.md"],
            ["../outside.md"],
            ["/absolute.md"],
            [".brain-history/HEAD"],
            ["work/setup/generated.md"],
            ["cache/result.json"],
            ["backups/old.md"],
            "profile/owner.md",
        ):
            with self.subTest(expected_paths=expected_paths):
                with self.assertRaises(history.HistoryError):
                    history.save(
                        self.root,
                        expected_paths=expected_paths,
                        summary="Rejected save",
                        reason="The declared scope is unsafe or inaccurate",
                    )
                self.assertEqual(
                    (self.history_directory / "HEAD").read_bytes(),
                    head_before,
                )
                self.assertFalse((self.history_directory / "LOCK").exists())

        self.assertEqual(history.inspect(self.root)["head"], initial_revision)
        self.assertEqual(
            history.changed_paths(self.root),
            ["profile/owner.md"],
        )

    def test_corrupt_reachable_blob_fails_closed(self) -> None:
        self.initialize()
        latest = history.list_revisions(self.root)[0]
        manifest_path = (
            self.history_directory / "manifests" / (latest["manifest"] + ".json")
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        blob_id = manifest["entries"]["profile/owner.md"]["blob"]
        blob_path = (
            self.history_directory / "blobs" / "sha256" / blob_id[:2] / blob_id[2:]
        )
        blob_path.write_bytes(b"tampered")

        with self.assertRaises(history.HistoryError):
            history.inspect(self.root)
        with self.assertRaises(history.HistoryError):
            history.list_revisions(self.root)

    def test_noncanonical_or_semantically_invalid_revision_fails_closed(self) -> None:
        initial_revision = self.initialize()
        revision_path = (
            self.history_directory / "revisions" / (initial_revision + ".json")
        )
        revision = json.loads(revision_path.read_text(encoding="utf-8"))
        revision_path.write_text(
            json.dumps(revision, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(history.HistoryError):
            history.inspect(self.root)

        # Restore the canonical object, then publish a correctly hashed revision
        # whose declared changed_paths contradicts its manifest.
        revision_path.write_bytes(history._canonical_bytes(revision) + b"\n")
        invalid_revision = dict(revision)
        invalid_revision["changed_paths"] = []
        invalid_revision_id = history._digest(
            history._canonical_bytes(invalid_revision)
        )
        invalid_path = (
            self.history_directory / "revisions" / (invalid_revision_id + ".json")
        )
        invalid_path.write_bytes(history._canonical_bytes(invalid_revision) + b"\n")
        (self.history_directory / "HEAD").write_bytes(
            (invalid_revision_id + "\n").encode("ascii")
        )
        with self.assertRaises(history.HistoryError):
            history.inspect(self.root)

    def test_lock_blocks_reads_and_writes_and_is_not_stolen(self) -> None:
        initial_revision = self.initialize()
        lock = self.history_directory / "LOCK"
        lock.write_bytes(b'{"owner":"another process"}\n')

        with self.assertRaises(history.HistoryError):
            history.inspect(self.root)
        with self.assertRaises(history.HistoryError):
            history.save(
                self.root,
                expected_paths=[],
                summary="Blocked save",
                reason="A concurrent writer owns the lock",
            )
        self.assertEqual(lock.read_bytes(), b'{"owner":"another process"}\n')

        lock.unlink()
        self.assertEqual(history.inspect(self.root)["head"], initial_revision)

    def test_stale_history_lock_requires_preview_token_and_live_lock_is_never_cleared(
        self,
    ) -> None:
        initial_revision = self.initialize()
        lock = self.write_history_lock(pid=os.getpid())

        with self.assertRaisesRegex(history.HistoryError, "active local process"):
            history.recovery_preview(self.root)
        self.assertTrue(lock.exists())

        lock.unlink()
        lock = self.write_history_lock(pid=999_999_999)
        with mock.patch.object(history, "_pid_is_alive", return_value=False):
            preview = history.recovery_preview(self.root)
            self.assertTrue(preview["needed"])
            self.assertEqual(preview["action"], "clear-stale-lock")
            self.assertTrue(preview["stale_lock"])
            self.assertTrue(lock.exists())

            with self.assertRaisesRegex(history.HistoryError, "stale or invalid"):
                history.recovery_apply(self.root, "f" * history.ID_LENGTH)
            self.assertTrue(lock.exists())

            result = history.recovery_apply(self.root, preview["token"])

        self.assertEqual(result["action"], "clear-stale-lock")
        self.assertEqual(result["head"], initial_revision)
        self.assertTrue(result["clean"])
        self.assertFalse(lock.exists())
        self.assertTrue(history.inspect(self.root)["clean"])

    def test_history_recovery_refuses_non_private_or_changed_lock(self) -> None:
        self.initialize()
        lock = self.write_history_lock(pid=999_999_999)
        os.chmod(str(lock), 0o644)
        with mock.patch.object(history, "_pid_is_alive", return_value=False):
            with self.assertRaisesRegex(history.HistoryError, "permissions"):
                history.recovery_preview(self.root)
        self.assertTrue(lock.exists())

        os.chmod(str(lock), history.PRIVATE_FILE_MODE)
        with mock.patch.object(history, "_pid_is_alive", return_value=False):
            preview = history.recovery_preview(self.root)
            changed_payload = json.loads(lock.read_text(encoding="utf-8"))
            changed_payload["nonce"] = "1" * 32
            lock.write_bytes(history._canonical_bytes(changed_payload) + b"\n")
            os.chmod(str(lock), history.PRIVATE_FILE_MODE)
            with self.assertRaisesRegex(
                history.HistoryError,
                "stale or invalid",
            ):
                history.recovery_apply(self.root, preview["token"])
        self.assertTrue(lock.exists())

    def test_tracked_symlink_and_hardlink_are_rejected(self) -> None:
        self.initialize()

        outside = Path(self.temporary_directory.name) / "outside.txt"
        outside.write_bytes(b"outside")
        linked = self.root / "projects" / "linked.txt"
        linked.parent.mkdir()
        try:
            os.symlink(str(outside), str(linked))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlinks are not available: %s" % exc)
        with self.assertRaises(history.HistoryError):
            history.changed_paths(self.root)
        linked.unlink()

        first = self.root / "projects" / "first.txt"
        second = self.root / "projects" / "second.txt"
        first.write_bytes(b"shared inode")
        try:
            os.link(str(first), str(second))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("hard links are not available: %s" % exc)
        with self.assertRaises(history.HistoryError):
            history.changed_paths(self.root)

    def test_head_is_not_published_when_save_fails(self) -> None:
        initial_revision = self.initialize()
        head_before = (self.history_directory / "HEAD").read_bytes()
        (self.root / "profile" / "owner.md").write_bytes(b"name: Unsaved\n")

        with mock.patch.object(
            history,
            "_write_head",
            side_effect=OSError("injected HEAD write failure"),
        ):
            with self.assertRaises(OSError):
                history.save(
                    self.root,
                    expected_paths=["profile/owner.md"],
                    summary="Interrupted save",
                    reason="Exercise atomic publication",
                )

        self.assertEqual(
            (self.history_directory / "HEAD").read_bytes(),
            head_before,
        )
        self.assertFalse((self.history_directory / "LOCK").exists())
        status = history.inspect(self.root)
        self.assertEqual(status["head"], initial_revision)
        self.assertFalse(status["clean"])
        self.assertEqual(status["modified"], ["profile/owner.md"])

    def test_undo_restores_exact_bytes_modes_and_records_forward_revision(self) -> None:
        original_alpha = b"line one\r\nline two  \r\n\x00tail"
        original_removed = b"\xff\x00removed\n"
        alpha = self.root / "alpha.bin"
        removed = self.root / "remove.bin"
        alpha.write_bytes(original_alpha)
        removed.write_bytes(original_removed)
        os.chmod(str(alpha), 0o640)
        os.chmod(str(removed), 0o600)
        initial_revision = self.initialize()

        alpha.write_bytes(b"replacement\n")
        os.chmod(str(alpha), 0o600)
        removed.unlink()
        added = self.root / "nested" / "added.bin"
        added.parent.mkdir()
        added.write_bytes(b"new\x00file")
        saved_revision = history.save(
            self.root,
            expected_paths=["alpha.bin", "nested/added.bin", "remove.bin"],
            summary="Create the second state",
            reason="Prepare add, modify, and delete undo coverage",
        )

        preview = history.undo_preview(self.root, initial_revision)
        self.assertEqual(preview["from_revision"], saved_revision)
        self.assertEqual(preview["to_revision"], initial_revision)
        self.assertEqual(
            preview["changes"],
            {
                "added": ["remove.bin"],
                "modified": ["alpha.bin"],
                "deleted": ["nested/added.bin"],
            },
        )

        undo_revision = history.undo_apply(
            self.root,
            to_revision=initial_revision,
            token=preview["token"],
            reason="Restore the byte-exact initial state",
        )
        self.assertNotIn(undo_revision, {initial_revision, saved_revision})
        self.assertEqual(alpha.read_bytes(), original_alpha)
        self.assertEqual(removed.read_bytes(), original_removed)
        self.assertFalse(added.exists())
        self.assertEqual(stat.S_IMODE(alpha.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(removed.stat().st_mode), 0o600)
        self.assertTrue(history.inspect(self.root)["clean"])

        revisions = history.list_revisions(self.root)
        newest = revisions[0]
        initial = revisions[-1]
        self.assertEqual(newest["id"], undo_revision)
        self.assertEqual(newest["kind"], "undo")
        self.assertEqual(newest["parent"], saved_revision)
        self.assertEqual(newest["undo_target"], initial_revision)
        self.assertEqual(newest["manifest"], initial["manifest"])

    def test_undo_handles_file_and_directory_shape_changes(self) -> None:
        shape = self.root / "shape"
        original = b"a file at the parent path"
        nested = b"a file below the parent path"
        shape.write_bytes(original)
        file_revision = self.initialize()

        shape.unlink()
        shape.mkdir()
        (shape / "child.bin").write_bytes(nested)
        directory_revision = history.save(
            self.root,
            expected_paths=["shape", "shape/child.bin"],
            summary="Turn a file into a directory",
            reason="Exercise path-shape transitions",
        )

        preview_file = history.undo_preview(self.root, file_revision)
        with mock.patch.object(
            history,
            "_write_head",
            side_effect=OSError("injected undo HEAD write failure"),
        ):
            with self.assertRaises(history.HistoryError):
                history.undo_apply(
                    self.root,
                    to_revision=file_revision,
                    token=preview_file["token"],
                    reason="Prove shape-changing rollback is lossless",
                )
        self.assertTrue(shape.is_dir())
        self.assertEqual((shape / "child.bin").read_bytes(), nested)
        self.assertEqual(history.inspect(self.root)["head"], directory_revision)
        self.assertTrue(history.inspect(self.root)["clean"])

        preview_file = history.undo_preview(self.root, file_revision)
        restored_file_revision = history.undo_apply(
            self.root,
            to_revision=file_revision,
            token=preview_file["token"],
            reason="Restore the parent-path file",
        )
        self.assertTrue(shape.is_file())
        self.assertEqual(shape.read_bytes(), original)
        self.assertTrue(history.inspect(self.root)["clean"])

        preview_directory = history.undo_preview(self.root, directory_revision)
        with mock.patch.object(
            history,
            "_write_head",
            side_effect=OSError("injected reverse-shape HEAD write failure"),
        ):
            with self.assertRaises(history.HistoryError):
                history.undo_apply(
                    self.root,
                    to_revision=directory_revision,
                    token=preview_directory["token"],
                    reason="Prove reverse shape rollback is lossless",
                )
        self.assertTrue(shape.is_file())
        self.assertEqual(shape.read_bytes(), original)
        self.assertEqual(
            history.inspect(self.root)["head"],
            restored_file_revision,
        )
        self.assertTrue(history.inspect(self.root)["clean"])

        preview_directory = history.undo_preview(self.root, directory_revision)
        history.undo_apply(
            self.root,
            to_revision=directory_revision,
            token=preview_directory["token"],
            reason="Restore the nested-path file",
        )
        self.assertTrue(shape.is_dir())
        self.assertEqual((shape / "child.bin").read_bytes(), nested)
        self.assertTrue(history.inspect(self.root)["clean"])

    def test_failed_undo_rolls_back_the_working_tree(self) -> None:
        original_removed = b"present initially"
        removed = self.root / "remove.bin"
        removed.write_bytes(original_removed)
        initial_revision = self.initialize()

        owner = self.root / "profile" / "owner.md"
        owner.write_bytes(b"name: Second state\n")
        removed.unlink()
        added = self.root / "added.bin"
        added.write_bytes(b"must survive a failed undo")
        saved_revision = history.save(
            self.root,
            expected_paths=["added.bin", "profile/owner.md", "remove.bin"],
            summary="Second state",
            reason="Prepare transaction rollback coverage",
        )
        expected_owner = owner.read_bytes()
        expected_added = added.read_bytes()
        preview = history.undo_preview(self.root, initial_revision)

        with mock.patch.object(
            history,
            "_materialize_entry",
            side_effect=RuntimeError("injected materialization failure"),
        ):
            with self.assertRaises(history.HistoryError):
                history.undo_apply(
                    self.root,
                    to_revision=initial_revision,
                    token=preview["token"],
                    reason="Exercise rollback",
                )

        self.assertEqual(owner.read_bytes(), expected_owner)
        self.assertEqual(added.read_bytes(), expected_added)
        self.assertFalse(removed.exists())
        status = history.inspect(self.root)
        self.assertEqual(status["head"], saved_revision)
        self.assertTrue(status["clean"])
        self.assertEqual(
            list((self.history_directory / "transactions").iterdir()),
            [],
        )
        self.assertFalse((self.history_directory / "RECOVERY_REQUIRED").exists())

    def test_recovery_rolls_back_an_unpublished_interrupted_undo(self) -> None:
        initial_revision, second_revision, added, removed = (
            self.prepare_undo_fixture()
        )
        owner = self.root / "profile" / "owner.md"
        expected_owner = owner.read_bytes()
        expected_added = added.read_bytes()
        preview = history.undo_preview(self.root, initial_revision)

        with mock.patch.object(
            history,
            "_materialize_entry",
            side_effect=SimulatedKill("simulated SIGKILL before publication"),
        ):
            with self.assertRaises(SimulatedKill):
                history.undo_apply(
                    self.root,
                    to_revision=initial_revision,
                    token=preview["token"],
                    reason="Interrupt before publishing HEAD",
                )

        transactions = list(
            (self.history_directory / "transactions").iterdir()
        )
        self.assertEqual(len(transactions), 1)
        self.assertFalse(added.exists())
        self.assertFalse(owner.exists())
        self.assertFalse(removed.exists())
        self.write_history_lock(pid=999_999_999)

        with mock.patch.object(history, "_pid_is_alive", return_value=False):
            recovery = history.recovery_preview(self.root)
            self.assertEqual(
                recovery["action"],
                "rollback-unpublished-undo",
            )
            self.assertEqual(recovery["head"], second_revision)
            result = history.recovery_apply(self.root, recovery["token"])

        self.assertEqual(result["action"], "rollback-unpublished-undo")
        self.assertEqual(result["head"], second_revision)
        self.assertEqual(owner.read_bytes(), expected_owner)
        self.assertEqual(added.read_bytes(), expected_added)
        self.assertFalse(removed.exists())
        self.assertEqual(
            list((self.history_directory / "transactions").iterdir()),
            [],
        )
        self.assertFalse((self.history_directory / "LOCK").exists())
        status = history.inspect(self.root)
        self.assertEqual(status["head"], second_revision)
        self.assertTrue(status["clean"])

    def test_recovery_cleans_an_already_published_interrupted_undo(self) -> None:
        initial_revision, second_revision, added, removed = (
            self.prepare_undo_fixture()
        )
        preview = history.undo_preview(self.root, initial_revision)

        with mock.patch.object(
            history.shutil,
            "rmtree",
            side_effect=SimulatedKill("simulated SIGKILL after publishing HEAD"),
        ):
            with self.assertRaises(SimulatedKill):
                history.undo_apply(
                    self.root,
                    to_revision=initial_revision,
                    token=preview["token"],
                    reason="Interrupt after publishing HEAD",
                )

        published_head = (self.history_directory / "HEAD").read_text(
            encoding="ascii"
        ).strip()
        self.assertNotIn(published_head, {initial_revision, second_revision})
        self.assertFalse(added.exists())
        self.assertTrue(removed.exists())
        self.assertEqual(
            len(list((self.history_directory / "transactions").iterdir())),
            1,
        )
        self.write_history_lock(pid=999_999_999)

        with mock.patch.object(history, "_pid_is_alive", return_value=False):
            recovery = history.recovery_preview(self.root)
            self.assertEqual(recovery["action"], "cleanup-published-undo")
            self.assertEqual(recovery["head"], published_head)
            result = history.recovery_apply(self.root, recovery["token"])

        self.assertEqual(result["action"], "cleanup-published-undo")
        self.assertEqual(result["head"], published_head)
        self.assertTrue(result["clean"])
        self.assertEqual(
            list((self.history_directory / "transactions").iterdir()),
            [],
        )
        self.assertFalse((self.history_directory / "LOCK").exists())
        newest = history.list_revisions(self.root)[0]
        self.assertEqual(newest["id"], published_head)
        self.assertEqual(newest["kind"], "undo")
        self.assertEqual(newest["undo_target"], initial_revision)

    def test_recovery_refuses_ambiguous_transactions_and_quarantine_drift(
        self,
    ) -> None:
        initial_revision, _second_revision, _added, _removed = (
            self.prepare_undo_fixture()
        )
        preview = history.undo_preview(self.root, initial_revision)
        with mock.patch.object(
            history,
            "_materialize_entry",
            side_effect=SimulatedKill("leave a partial transaction"),
        ):
            with self.assertRaises(SimulatedKill):
                history.undo_apply(
                    self.root,
                    to_revision=initial_revision,
                    token=preview["token"],
                    reason="Prepare ambiguity checks",
                )

        transaction = next(
            (self.history_directory / "transactions").iterdir()
        )
        quarantine_file = next(
            path
            for path in (transaction / "quarantine").rglob("*")
            if path.is_file()
        )
        original = quarantine_file.read_bytes()
        quarantine_file.write_bytes(b"tampered quarantine")
        os.chmod(str(quarantine_file), history.PRIVATE_FILE_MODE)
        with self.assertRaisesRegex(
            history.HistoryError,
            "quarantine does not match",
        ):
            history.recovery_preview(self.root)
        quarantine_file.write_bytes(original)
        os.chmod(str(quarantine_file), history.PRIVATE_FILE_MODE)

        duplicate = transaction.parent / ("f" * 32)
        history.shutil.copytree(transaction, duplicate)
        with self.assertRaisesRegex(
            history.HistoryError,
            "Multiple incomplete",
        ):
            history.recovery_preview(self.root)
        self.assertTrue(transaction.exists())
        self.assertTrue(duplicate.exists())

    def test_undo_rejects_stale_confirmation_token(self) -> None:
        initial_revision = self.initialize()
        owner = self.root / "profile" / "owner.md"
        owner.write_bytes(b"name: Second\n")
        history.save(
            self.root,
            expected_paths=["profile/owner.md"],
            summary="Second state",
            reason="Create an undo source",
        )
        stale_preview = history.undo_preview(self.root, initial_revision)

        later = self.root / "later.md"
        later.write_bytes(b"third state")
        latest_revision = history.save(
            self.root,
            expected_paths=["later.md"],
            summary="Third state",
            reason="Invalidate the earlier confirmation",
        )
        expected_owner = owner.read_bytes()
        expected_later = later.read_bytes()

        with self.assertRaises(history.HistoryError):
            history.undo_apply(
                self.root,
                to_revision=initial_revision,
                token=stale_preview["token"],
                reason="This confirmation must no longer apply",
            )

        self.assertEqual(owner.read_bytes(), expected_owner)
        self.assertEqual(later.read_bytes(), expected_later)
        self.assertEqual(history.inspect(self.root)["head"], latest_revision)
        self.assertTrue(history.inspect(self.root)["clean"])


if __name__ == "__main__":
    unittest.main()
