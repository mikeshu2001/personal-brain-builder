#!/usr/bin/env python3
"""Deterministic control plane for the build-personal-brain skill.

The language model handles meaning, explanations, and ambiguity. This tool
handles repeatable filesystem operations, checkpoints, validation, bounded
connection blocks, and local history.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import difflib
import fnmatch
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import stat as statlib
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterable

from state_control import (
    AREA_COVERAGE,
    AREA_EXECUTION,
    TOP_LEVEL_STATES,
    StateTransitionError,
    StateValidationError,
    access_decision,
    has_active_consent,
    normalize_scopes,
    ready_gate_errors,
    transition_state,
    validate_state,
)
from card_engine import (
    contains_secret_text,
    contains_secret_value_text,
    search_cards,
    validate_cards,
)
from portable_history import (
    HistoryError,
    initialize as history_initialize,
    inspect as history_inspect,
    list_revisions as history_list_revisions,
    recovery_apply as history_recovery_apply,
    recovery_preview as history_recovery_preview,
    save as history_save,
    undo_apply as history_undo_apply,
    undo_preview as history_undo_preview,
)


SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "brain-template"
POINTER_TEMPLATE = SKILL_ROOT / "assets" / "runner-pointer.md"

KNOWLEDGE_ROOTS = ("profile", "projects", "rules", "links", "journal", "inferred")
NON_CANONICAL_ROOTS = ("inbox", "system", "work", "examples")
REQUIRED_DIRS = (
    "profile",
    "projects",
    "rules",
    "links/people",
    "links/entities",
    "links/relations",
    "links/decisions",
    "journal",
    "inferred",
    "inbox",
    "system/templates",
    "system/runtime/scripts",
    "system/runtime/assets",
    "system/runtime/guides",
    "work/setup/snapshots",
    "work/setup/backups",
    "work/setup/transactions",
    "examples",
)
REQUIRED_RUNTIME_FILES = (
    "system/runtime/scripts/brainctl.py",
    "system/runtime/scripts/state_control.py",
    "system/runtime/scripts/card_engine.py",
    "system/runtime/scripts/portable_history.py",
    "system/runtime/assets/runner-pointer.md",
    "system/runtime/guides/maintenance.md",
    "system/runtime/guides/safety-rules.md",
    "system/runtime/README.md",
)

STATES = set(TOP_LEVEL_STATES)

ALLOWED_TYPES = {
    "project",
    "rule",
    "decision",
    "person",
    "entity",
    "link",
    "observation",
    "journal",
    "conflict",
}
ALLOWED_ORIGINS = {"stated", "imported", "inferred", "observation"}
SCOPE_MODES = {"metadata", "content", "names-only", "exclude"}
AREA_STATUSES = set(AREA_COVERAGE)
CHECK_STATUSES = {"pass", "fail", "blocked", "skipped"}
SESSION_COVERAGE = {
    "pending",
    "metadata-only",
    "distillates-reviewed",
    "transcripts-reviewed",
    "excluded-by-user",
    "unavailable",
    "blocked-sensitive",
    "needs-recheck",
}
CONNECTION_STATUSES = {
    "not-detected",
    "unsupported",
    "manual-step-required",
    "pending-approval",
    "connected",
    "verified",
    "failed-closed",
}
CONSENT_DECISIONS = {"approved", "declined", "revoked"}
CONSENT_KINDS = {
    "introduction",
    "memory-location",
    "metadata-map",
    "content-audit",
    "session-history-metadata",
    "session-history",
    "connection-read",
    "connection",
    "recovery-copy",
    "publication",
    "deletion",
}
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
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CARD_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)+$")
WIKILINK_RE = re.compile(r"\[\[([a-z0-9][a-z0-9/-]*)\]\]")

START_MARKER = "<!-- START PERSONAL BRAIN -->"
END_MARKER = "<!-- END PERSONAL BRAIN -->"
CORE_PROFILE_PLACEHOLDERS = (
    "To be completed during onboarding.",
    "Other confirmed preferences will be added during onboarding.",
    "Safety choices will be added during onboarding.",
    "{{OWNER_NAME}}",
    "{{LOCALE}}",
    "{{CREATED_DATE}}",
)
CORE_REQUIRED_STATES = {
    "LIBRARY_VALIDATION",
    "CONNECTION_REVIEW",
    "CONNECTION_APPLY",
    "ACCEPTANCE_TESTING",
    "RECOVERY_COPY_REVIEW",
    "HANDOFF",
    "READY",
}

SENSITIVE_COMPONENTS = {
    ".ssh",
    ".gnupg",
    "keychain",
    "keychains",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "wallet",
    "wallets",
    "browser profiles",
}
CLOUD_OR_SHARED_COMPONENTS = {
    "dropbox",
    "onedrive",
    "google drive",
    "icloud drive",
    "box",
    "shared",
    "public",
}


class BrainCtlError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return dt.date.today().isoformat()


def is_iso_date(value: str) -> bool:
    if not DATE_RE.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def output(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def fail(message: str, *, details: Any | None = None, code: int = 2) -> None:
    payload: dict[str, Any] = {"ok": False, "error": message}
    if details is not None:
        payload["details"] = details
    output(payload)
    raise SystemExit(code)


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    if path.is_symlink():
        raise BrainCtlError(f"Refusing to replace a symbolic link: {path}")
    if path.parent.is_symlink():
        raise BrainCtlError(f"Refusing to write through a symbolic-link directory: {path.parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.is_dir():
        raise BrainCtlError(f"Write parent is not a directory: {path.parent}")
    existing_mode = mode
    if existing_mode is None and path.exists():
        existing_mode = path.stat().st_mode & 0o777
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def atomic_replace_preserving_metadata(
    path: Path,
    data: bytes,
    *,
    expected_before: bytes | None = None,
    expected_fingerprint: tuple[Any, ...] | None = None,
) -> None:
    metadata: dict[str, Any] | None = None
    if path.exists() or path.is_symlink():
        stat_result = path.lstat()
        if statlib.S_ISLNK(stat_result.st_mode):
            raise BrainCtlError(f"Refusing to replace a symlink: {path}")
        if not statlib.S_ISREG(stat_result.st_mode):
            raise BrainCtlError(f"Refusing to replace a non-regular file: {path}")
        metadata = {
            "mode": stat_result.st_mode & 0o777,
            "uid": getattr(stat_result, "st_uid", None),
            "gid": getattr(stat_result, "st_gid", None),
            "xattrs": {},
        }
        if hasattr(os, "listxattr"):
            for name in os.listxattr(path):
                try:
                    metadata["xattrs"][name] = os.getxattr(path, name)
                except OSError:
                    continue
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if metadata is not None:
            os.chmod(tmp_path, metadata["mode"])
            if hasattr(os, "chown") and metadata["uid"] is not None:
                try:
                    os.chown(tmp_path, metadata["uid"], metadata["gid"])
                except PermissionError:
                    if tmp_path.stat().st_uid != metadata["uid"]:
                        raise
            if hasattr(os, "setxattr"):
                for name, value in metadata["xattrs"].items():
                    os.setxattr(tmp_path, name, value)
        if expected_before is not None or expected_fingerprint is not None:
            current_fingerprint = connection_target_fingerprint(path)
            current_bytes = read_connection_target(path)
            verified_fingerprint = connection_target_fingerprint(path)
            if (
                current_fingerprint != verified_fingerprint
                or (
                    expected_fingerprint is not None
                    and current_fingerprint != expected_fingerprint
                )
                or (
                    expected_before is not None
                    and not hmac.compare_digest(current_bytes, expected_before)
                )
            ):
                raise BrainCtlError(
                    f"Connection target changed before atomic replacement: {path}"
                )
        os.replace(tmp_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_regular_bytes(
    path: Path,
    *,
    max_size: int = 10_000_000,
    description: str = "file",
) -> bytes:
    try:
        expected = path.lstat()
    except FileNotFoundError as exc:
        raise BrainCtlError(f"Missing {description}: {path}") from exc
    if not statlib.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
        raise BrainCtlError(f"{description.capitalize()} must be one regular file: {path}")
    if hasattr(os, "getuid") and expected.st_uid != os.getuid():
        raise BrainCtlError(f"{description.capitalize()} belongs to another user: {path}")
    if expected.st_size > max_size:
        raise BrainCtlError(f"{description.capitalize()} is unexpectedly large: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrainCtlError(f"Cannot safely open {description} {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not statlib.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino)
            != (expected.st_dev, expected.st_ino)
        ):
            raise BrainCtlError(
                f"{description.capitalize()} changed identity while opening: {path}"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                raise BrainCtlError(
                    f"{description.capitalize()} became unexpectedly large: {path}"
                )
            chunks.append(chunk)
        after = os.fstat(descriptor)
        before_signature = (
            opened.st_dev,
            opened.st_ino,
            opened.st_nlink,
            opened.st_size,
            getattr(opened, "st_mtime_ns", int(opened.st_mtime * 1_000_000_000)),
            getattr(opened, "st_ctime_ns", int(opened.st_ctime * 1_000_000_000)),
        )
        after_signature = (
            after.st_dev,
            after.st_ino,
            after.st_nlink,
            after.st_size,
            getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
            getattr(after, "st_ctime_ns", int(after.st_ctime * 1_000_000_000)),
        )
        if before_signature != after_signature:
            raise BrainCtlError(
                f"{description.capitalize()} changed while reading: {path}"
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_regular_utf8(
    path: Path,
    *,
    max_size: int = 10_000_000,
    description: str = "file",
) -> str:
    raw = read_regular_bytes(path, max_size=max_size, description=description)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrainCtlError(f"{description.capitalize()} is not UTF-8: {path}") from exc


def read_json(path: Path) -> Any:
    try:
        expected = path.lstat()
    except FileNotFoundError as exc:
        raise BrainCtlError(f"Missing required file: {path}") from exc
    if not statlib.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
        raise BrainCtlError(f"JSON file must be one private regular file: {path}")
    if expected.st_mode & 0o077:
        raise BrainCtlError(
            f"Private JSON file permissions allow another local account: {path}"
        )
    if hasattr(os, "getuid") and expected.st_uid != os.getuid():
        raise BrainCtlError(f"JSON file belongs to another user: {path}")
    if expected.st_size > 10_000_000:
        raise BrainCtlError(f"JSON file is unexpectedly large: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrainCtlError(f"Cannot safely open JSON file {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not statlib.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino)
            != (expected.st_dev, expected.st_ino)
        ):
            raise BrainCtlError(f"JSON file changed identity while opening: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 10_000_000:
                raise BrainCtlError(f"JSON file became unexpectedly large: {path}")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_nlink,
            after.st_size,
            getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_nlink,
            opened.st_size,
            getattr(opened, "st_mtime_ns", int(opened.st_mtime * 1_000_000_000)),
        ):
            raise BrainCtlError(f"JSON file changed while reading: {path}")
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrainCtlError(f"JSON file is not UTF-8: {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BrainCtlError(f"Invalid JSON in {path}: {exc}") from exc


def resolve_path(raw: str | Path) -> Path:
    return Path(raw).expanduser().resolve()


def assert_not_broad_path(path: Path, *, purpose: str) -> None:
    forbidden = {Path("/").resolve(), Path.home().resolve()}
    if os.name == "nt":
        forbidden.add(Path(path.anchor).resolve())
    if path in forbidden:
        raise BrainCtlError(f"Refusing {purpose} on broad path: {path}")


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def state_path(brain_root: Path) -> Path:
    return brain_root / "work" / "setup" / "state.json"


def events_path(brain_root: Path) -> Path:
    return brain_root / "work" / "setup" / "events.jsonl"


def operation_lock_path(brain_root: Path) -> Path:
    return brain_root / "work" / "setup" / "operation.lock"


def assert_safe_brain_operating_layout(brain_root: Path) -> None:
    """Reject redirected or shared operating-state components."""

    for relative in (
        "work",
        "work/setup",
        "work/setup/snapshots",
        "work/setup/backups",
        "work/setup/transactions",
    ):
        path = brain_root / relative
        try:
            item = path.lstat()
        except FileNotFoundError as exc:
            raise BrainCtlError(
                f"Missing private operating directory: {path}"
            ) from exc
        if statlib.S_ISLNK(item.st_mode) or not statlib.S_ISDIR(item.st_mode):
            raise BrainCtlError(
                f"Private operating path must be a real directory, not a link: {path}"
            )
        if hasattr(os, "getuid") and item.st_uid != os.getuid():
            raise BrainCtlError(
                f"Private operating directory belongs to another user: {path}"
            )
    for relative in (
        "work/setup/state.json",
        "work/setup/events.jsonl",
        "work/setup/operation.lock",
    ):
        path = brain_root / relative
        if not path.exists() and not path.is_symlink():
            continue
        item = path.lstat()
        if (
            statlib.S_ISLNK(item.st_mode)
            or not statlib.S_ISREG(item.st_mode)
            or item.st_nlink != 1
        ):
            raise BrainCtlError(
                f"Private operating file must be one regular file: {path}"
            )
        if hasattr(os, "getuid") and item.st_uid != os.getuid():
            raise BrainCtlError(
                f"Private operating file belongs to another user: {path}"
            )


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def setup_operation_lock(brain_root: Path, purpose: str) -> Iterable[None]:
    assert_safe_brain_operating_layout(brain_root)
    path = operation_lock_path(brain_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "schema_version": 1,
        "token": token,
        "pid": os.getpid(),
        "host": platform.node(),
        "purpose": purpose,
        "created_at": utc_now(),
    }
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BrainCtlError(
            f"Another memory operation or an interrupted lock exists: {path}"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            current = read_json(path)
        except BrainCtlError:
            current = {}
        if current.get("token") == token:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def cmd_lock(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    load_state(brain_root)
    path = operation_lock_path(brain_root)
    if not path.exists():
        output({"ok": True, "locked": False, "path": str(path)})
        return
    payload = read_json(path)
    same_host = payload.get("host") == platform.node()
    active = (
        same_host
        and isinstance(payload.get("pid"), int)
        and pid_is_alive(payload["pid"])
    )
    token = hashlib.sha256(path.read_bytes()).hexdigest()
    if args.action == "inspect":
        output(
            {
                "ok": True,
                "locked": True,
                "active_process": active,
                "details": payload,
                "clear_token": None if active else token,
            }
        )
        return
    if active:
        raise BrainCtlError("The lock belongs to an active local process")
    if args.action == "clear-preview":
        output(
            {
                "ok": True,
                "locked": True,
                "active_process": False,
                "clear_token": token,
                "details": payload,
            }
        )
        return
    if args.token != token:
        raise BrainCtlError("Lock clear token does not match the current stale lock")
    path.unlink()
    output({"ok": True, "cleared": True, "path": str(path)})


def load_state(
    brain_root: Path,
    *,
    allow_pending_pointer: bool = False,
) -> dict[str, Any]:
    assert_safe_brain_operating_layout(brain_root)
    state = read_json(state_path(brain_root))
    if state.get("schema_version") != 1:
        raise BrainCtlError("Unsupported setup state version")
    recorded = resolve_path(state.get("brain_root", ""))
    if recorded != brain_root:
        raise BrainCtlError(
            f"State points to a different brain root: {recorded} (opened {brain_root})"
        )
    if "state" not in state and "stage" in state:
        state["state"] = state["stage"]
    try:
        validate_state(state, brain_root)
    except StateValidationError as exc:
        raise BrainCtlError(str(exc)) from exc
    events = state.get("events")
    if not isinstance(events, list) or int(state.get("event_seq", -1)) != len(events):
        raise BrainCtlError("Setup state event sequence is inconsistent")
    pointer_raw = state.get("builder_pointer")
    if pointer_raw:
        pointer_lexical = Path(pointer_raw).expanduser().absolute()
        if pointer_lexical.is_symlink():
            raise BrainCtlError(
                f"Builder pointer must not be a symbolic link: {pointer_lexical}"
            )
        pointer_path = pointer_lexical.resolve(strict=False)
        if str(pointer_path) != pointer_raw:
            raise BrainCtlError("Builder pointer path in state is not canonical")
        if pointer_path.exists():
            pointer = read_json(pointer_path)
            if (
                isinstance(pointer, dict)
                and pointer.get("status") == "pending-creation"
            ):
                if not allow_pending_pointer:
                    raise BrainCtlError(
                        "Memory creation was interrupted before its pointer was finalized"
                    )
                claim = _validated_pointer_claim(pointer_path)
                if (
                    claim["brain_root"] != str(brain_root)
                    or claim["owner"] != state.get("owner")
                    or claim["locale"] != state.get("locale")
                ):
                    raise BrainCtlError(
                        "Pending builder pointer disagrees with memory state"
                    )
            else:
                for key in ("brain_id", "installation_id", "brain_root"):
                    expected = state[key]
                    actual = pointer.get(key)
                    if key == "brain_root" and actual:
                        actual = str(resolve_path(actual))
                    if actual != expected:
                        raise BrainCtlError(
                            f"Builder pointer disagrees with memory state for {key}"
                        )
    return state


def save_state(brain_root: Path, state: dict[str, Any], event_name: str, details: Any = None) -> None:
    assert_safe_brain_operating_layout(brain_root)
    state["updated_at"] = utc_now()
    state["event_seq"] = int(state.get("event_seq", 0)) + 1
    event = {
        "seq": state["event_seq"],
        "at": state["updated_at"],
        "event": event_name,
    }
    if details is not None:
        event["details"] = details
    state.setdefault("events", []).append(event)
    if int(state.get("event_seq", -1)) != len(state.get("events", [])):
        raise BrainCtlError(
            "Refusing to save an inconsistent setup event sequence"
        )
    try:
        validate_state(state, brain_root)
    except StateValidationError as exc:
        raise BrainCtlError(
            "Refusing to save invalid durable setup state: " + str(exc)
        ) from exc
    atomic_write_json(state_path(brain_root), state)
    path = events_path(brain_root)
    rendered = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in state["events"]
    )
    try:
        atomic_write_text(path, rendered)
    except OSError:
        # state.json is canonical; events.jsonl is a derived inspection aid.
        pass


def run_git(brain_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", "--no-optional-locks", *args]
    result = subprocess.run(
        command,
        cwd=brain_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise BrainCtlError(
            f"Git command failed: {' '.join(args)}\n{result.stderr.strip()}"
        )
    return result


def git_available() -> bool:
    return shutil.which("git") is not None


def git_status_paths(brain_root: Path) -> list[str]:
    paths: set[str] = set()
    commands = (
        ["diff", "--name-only", "-z"],
        ["diff", "--cached", "--name-only", "-z"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    for command in commands:
        result = run_git(brain_root, command)
        paths.update(value for value in result.stdout.split("\0") if value)
    return sorted(paths)


def git_is_clean(brain_root: Path) -> bool:
    return not git_status_paths(brain_root)


def load_ignore_patterns(brain_root: Path) -> list[str]:
    path = brain_root / ".brainignore"
    patterns: list[str] = []
    if path.exists() or path.is_symlink():
        item = path.lstat()
        if (
            not statlib.S_ISREG(item.st_mode)
            or item.st_nlink != 1
            or path.is_symlink()
        ):
            raise BrainCtlError(".brainignore must be one regular file")
        if item.st_size > 1_000_000:
            raise BrainCtlError(".brainignore is unexpectedly large")
        for line in read_regular_utf8(
            path,
            max_size=1_000_000,
            description=".brainignore",
        ).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                patterns.append(stripped)
    return patterns


def sensitive_category(path: Path, relative: Path, patterns: Iterable[str]) -> str | None:
    lower_parts = {part.casefold() for part in path.parts}
    if lower_parts & SENSITIVE_COMPONENTS:
        return "protected-location"
    rel = relative.as_posix()
    name = path.name
    for pattern in patterns:
        normalized = pattern.replace("**/", "")
        if (
            fnmatch.fnmatch(rel, pattern)
            or fnmatch.fnmatch(name, pattern)
            or fnmatch.fnmatch(rel, normalized)
        ):
            return "ignored-pattern"
    lower_name = name.casefold()
    if lower_name.startswith(".env") or lower_name in {
        "auth.json",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
    }:
        return "credentials"
    return None


def ensure_approved(
    state: dict[str, Any],
    path: Path,
    modes: set[str],
    *,
    operation: str | None = None,
) -> dict[str, Any]:
    if operation is not None:
        decision = access_decision(state, path, operation)
        if not decision.allowed:
            raise BrainCtlError(
                f"Path is not approved for {operation}: {path} ({decision.reason})"
            )
    matches = []
    for item in state.get("scope", []):
        root = resolve_path(item["path"])
        if is_within(path, root):
            matches.append((len(root.parts), item))
    if not matches:
        raise BrainCtlError(f"Path is not approved for this operation: {path}")
    selected = sorted(matches, key=lambda pair: pair[0], reverse=True)[0][1]
    if selected.get("mode") == "exclude":
        raise BrainCtlError(f"Path is explicitly excluded: {path}")
    if selected.get("mode") not in modes:
        raise BrainCtlError(
            f"Path is not approved for this operation mode: {path}"
        )
    return selected


def substitute_template_tree(root: Path, replacements: dict[str, str]) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = read_regular_utf8(
                path,
                max_size=10_000_000,
                description="template file",
            )
        except UnicodeDecodeError:
            continue
        for key, value in replacements.items():
            text = text.replace("{{" + key + "}}", value)
        atomic_write_text(path, text)


def doctor_report(
    candidate: Path | None,
    *,
    lexical_symlink: bool = False,
    pointer_path: Path | None = None,
    pointer_lexical_symlink: bool = False,
) -> dict[str, Any]:
    parent = candidate.parent if candidate else Path.cwd()
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    location_risks: list[str] = []
    if candidate:
        candidate_text = str(candidate)
        if (
            any(character in candidate_text for character in ("\r", "\n", "\0", "`"))
            or START_MARKER in candidate_text
            or END_MARKER in candidate_text
        ):
            location_risks.append("candidate-path-is-unsafe-for-assistant-connection")
        if lexical_symlink:
            location_risks.append("candidate-is-symbolic-link")
        folded_parts = {part.casefold() for part in candidate.parts}
        matched = sorted(folded_parts & CLOUD_OR_SHARED_COMPONENTS)
        if matched:
            location_risks.append(
                "path-name-suggests-cloud-or-shared-location:" + ",".join(matched)
            )
        if candidate.exists():
            mode = candidate.stat().st_mode & 0o777
            if mode & 0o077:
                location_risks.append("candidate-permissions-allow-other-users")
    result: dict[str, Any] = {
        "ok": True,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_supported": sys.version_info >= (3, 9),
        "git_available": git_available(),
        "current_directory": str(Path.cwd()),
        "candidate_path": str(candidate) if candidate else None,
        "candidate_exists": candidate.exists() if candidate else None,
        "candidate_empty": (
            candidate.is_dir() and not any(candidate.iterdir())
            if candidate and candidate.exists()
            else None
        ),
        "parent_writable": os.access(parent, os.W_OK),
        "free_bytes": shutil.disk_usage(parent).free,
        "location_risks": location_risks,
        "network_or_sync_detection": "heuristic-only",
        "pointer_path": str(pointer_path) if pointer_path else None,
        "pointer_exists": pointer_path.exists() if pointer_path else None,
        "pointer_parent_exists": (
            pointer_path.parent.exists() if pointer_path else None
        ),
        "pointer_parent_writable": (
            pointer_path.parent.is_dir()
            and os.access(pointer_path.parent, os.W_OK)
            if pointer_path
            else None
        ),
        "pointer_is_symbolic_link": (
            pointer_lexical_symlink if pointer_path else None
        ),
    }
    result["ok"] = bool(
        result["python_supported"]
        and result["parent_writable"]
        and not lexical_symlink
        and "candidate-path-is-unsafe-for-assistant-connection"
        not in location_risks
        and (
            pointer_path is None
            or (
                result["pointer_parent_exists"]
                and result["pointer_parent_writable"]
                and not pointer_lexical_symlink
            )
        )
    )
    if candidate is not None:
        parent_stat = parent.stat()
        pointer_parent_stat = (
            pointer_path.parent.stat()
            if pointer_path is not None and pointer_path.parent.exists()
            else None
        )
        token_payload = {
            "candidate_path": str(candidate),
            "candidate_exists": result["candidate_exists"],
            "candidate_empty": result["candidate_empty"],
            "parent_path": str(parent.resolve()),
            "parent_device": parent_stat.st_dev,
            "parent_inode": parent_stat.st_ino,
            "location_risks": result["location_risks"],
            "pointer_path": str(pointer_path) if pointer_path else None,
            "pointer_exists": result["pointer_exists"],
            "pointer_parent_path": (
                str(pointer_path.parent.resolve()) if pointer_path else None
            ),
            "pointer_parent_device": (
                pointer_parent_stat.st_dev if pointer_parent_stat else None
            ),
            "pointer_parent_inode": (
                pointer_parent_stat.st_ino if pointer_parent_stat else None
            ),
            "pointer_is_symbolic_link": pointer_lexical_symlink,
        }
        result["creation_preview_token"] = hashlib.sha256(
            json.dumps(
                token_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    else:
        result["creation_preview_token"] = None
    return result


def cmd_doctor(args: argparse.Namespace) -> None:
    lexical = Path(args.path).expanduser().absolute() if args.path else None
    lexical_symlink = bool(lexical and lexical.is_symlink())
    candidate = lexical.resolve(strict=False) if lexical else None
    pointer_lexical = (
        Path(args.pointer_file).expanduser().absolute()
        if args.pointer_file
        else (Path.cwd() / ".personal-brain-pointer.json")
        if candidate is not None
        else None
    )
    pointer_lexical_symlink = bool(
        pointer_lexical and pointer_lexical.is_symlink()
    )
    pointer_path = (
        pointer_lexical.resolve(strict=False) if pointer_lexical else None
    )
    result = doctor_report(
        candidate,
        lexical_symlink=lexical_symlink,
        pointer_path=pointer_path,
        pointer_lexical_symlink=pointer_lexical_symlink,
    )
    output(result)
    if not result["ok"]:
        raise SystemExit(1)


POINTER_CLAIM_FIELDS = {
    "schema_version",
    "status",
    "claim_id",
    "created_at",
    "pid",
    "host",
    "brain_root",
    "owner",
    "locale",
    "preview_token",
}


def _write_exclusive_private_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise BrainCtlError(f"Refusing to replace an existing pointer: {path}")
    try:
        parent = path.parent.lstat()
    except FileNotFoundError as exc:
        raise BrainCtlError(f"Pointer parent directory does not exist: {path.parent}") from exc
    if statlib.S_ISLNK(parent.st_mode) or not statlib.S_ISDIR(parent.st_mode):
        raise BrainCtlError(f"Pointer parent must be one real directory: {path.parent}")
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BrainCtlError(
            "Another memory creation already reserved this exact pointer file"
        ) from exc
    created_identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (
                created_identity.st_dev,
                created_identity.st_ino,
            ):
                path.unlink()
        except OSError:
            pass
        raise


def _validated_pointer_claim(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict) or set(payload) != POINTER_CLAIM_FIELDS:
        raise BrainCtlError(f"Pointer reservation has an unexpected schema: {path}")
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "pending-creation"
        or not isinstance(payload.get("claim_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", payload["claim_id"])
        or not isinstance(payload.get("pid"), int)
        or payload["pid"] <= 0
        or not all(
            isinstance(payload.get(field), str) and payload[field]
            for field in (
                "created_at",
                "host",
                "brain_root",
                "owner",
                "locale",
                "preview_token",
            )
        )
        or not re.fullmatch(r"[0-9a-f]{64}", payload["preview_token"])
    ):
        raise BrainCtlError(f"Pointer reservation is invalid: {path}")
    if str(Path(payload["brain_root"]).expanduser().resolve(strict=False)) != payload[
        "brain_root"
    ]:
        raise BrainCtlError(f"Pointer reservation contains a non-canonical path: {path}")
    return payload


def _pointer_claim_is_active(payload: dict[str, Any]) -> bool:
    if payload.get("host") != (platform.node() or "local-host-unknown"):
        # A process on another host cannot be checked safely from here.
        return True
    return pid_is_alive(payload["pid"])


def _pointer_claim_digest(path: Path) -> str:
    return hashlib.sha256(
        read_regular_bytes(path, max_size=100_000, description="pointer reservation")
    ).hexdigest()


def _remove_pointer_claim_if_exact(
    path: Path,
    *,
    claim_id: str,
    expected_digest: str | None = None,
) -> bool:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return False
    payload = _validated_pointer_claim(path)
    digest = _pointer_claim_digest(path)
    after = path.lstat()
    signature_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)),
        getattr(before, "st_ctime_ns", int(before.st_ctime * 1_000_000_000)),
    )
    signature_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
        getattr(after, "st_ctime_ns", int(after.st_ctime * 1_000_000_000)),
    )
    if (
        payload["claim_id"] != claim_id
        or signature_before != signature_after
        or (expected_digest is not None and digest != expected_digest)
    ):
        return False
    path.unlink()
    return True


def _finalize_pointer_claim(
    path: Path,
    *,
    claim_id: str,
    pointer: dict[str, Any],
) -> None:
    payload = _validated_pointer_claim(path)
    if payload["claim_id"] != claim_id:
        raise BrainCtlError("Pointer reservation changed before finalization")
    atomic_write_json(path, pointer)


def cmd_init_claim(args: argparse.Namespace) -> None:
    lexical = Path(args.pointer_file).expanduser().absolute()
    if lexical.is_symlink():
        raise BrainCtlError(f"Refusing a symbolic-link pointer reservation: {lexical}")
    path = lexical.resolve(strict=False)
    payload = _validated_pointer_claim(path)
    active = _pointer_claim_is_active(payload)
    memory_state_exists = state_path(
        Path(payload["brain_root"]).resolve(strict=False)
    ).exists()
    digest = _pointer_claim_digest(path)
    token_payload = {
        "action": "clear-interrupted-pointer-reservation",
        "path": str(path),
        "digest": digest,
        "claim_id": payload["claim_id"],
    }
    clear_token = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if args.action == "inspect":
        output(
            {
                "ok": True,
                "reserved": True,
                "active_or_remote_process": active,
                "memory_state_exists": memory_state_exists,
                "details": payload,
                "clear_token": None if active or memory_state_exists else clear_token,
            }
        )
        return
    if active:
        raise BrainCtlError(
            "The pointer reservation may belong to an active process and cannot be cleared"
        )
    if memory_state_exists:
        raise BrainCtlError(
            "A created memory exists; rerun init with the same details to finalize its pointer"
        )
    if args.action == "clear-preview":
        output(
            {
                "ok": True,
                "reserved": True,
                "active_or_remote_process": False,
                "memory_state_exists": False,
                "clear_token": clear_token,
                "details": payload,
            }
        )
        return
    if args.token != clear_token:
        raise BrainCtlError(
            "Pointer reservation clear token does not match the current file"
        )
    if not _remove_pointer_claim_if_exact(
        path,
        claim_id=payload["claim_id"],
        expected_digest=digest,
    ):
        raise BrainCtlError("Pointer reservation changed before it could be cleared")
    output(
        {
            "ok": True,
            "cleared": True,
            "pointer_file": str(path),
            "next_action": "Run the location check again before creating memory.",
        }
    )


def cmd_init(args: argparse.Namespace) -> None:
    owner_name = args.owner.strip()
    locale = args.locale.strip()
    if (
        not owner_name
        or len(owner_name) > 200
        or any(character in owner_name for character in ('"', "\r", "\n"))
    ):
        raise BrainCtlError(
            "Owner display name must contain 1 to 200 safe single-line characters"
        )
    if (
        not locale
        or len(locale) > 100
        or any(character in locale for character in ('"', "\r", "\n"))
    ):
        raise BrainCtlError(
            "User language must contain 1 to 100 safe single-line characters"
        )
    ensure_record_text_is_safe(
        {"owner_display_name": owner_name, "user_language": locale}
    )
    lexical_brain_root = Path(args.brain_root).expanduser().absolute()
    if lexical_brain_root.is_symlink():
        raise BrainCtlError(
            f"Refusing to create memory through a symbolic link: {lexical_brain_root}"
        )
    brain_root = lexical_brain_root.resolve(strict=False)
    lexical_pointer_path = (
        Path(args.pointer_file).expanduser().absolute()
        if args.pointer_file
        else Path.cwd() / ".personal-brain-pointer.json"
    )
    if lexical_pointer_path.is_symlink():
        raise BrainCtlError(
            f"Refusing to use a symbolic-link builder pointer: {lexical_pointer_path}"
        )
    pointer_path = lexical_pointer_path.resolve(strict=False)
    assert_not_broad_path(brain_root, purpose="brain creation")
    if not args.approval_note.strip():
        raise BrainCtlError("Memory creation requires a non-empty approval note")
    ensure_record_text_is_safe({"approval_note": args.approval_note})

    claim: dict[str, Any] | None = None
    claim_created_here = False
    if pointer_path.exists():
        existing = read_json(pointer_path)
        if (
            isinstance(existing, dict)
            and existing.get("status") == "pending-creation"
        ):
            claim = _validated_pointer_claim(pointer_path)
            if _pointer_claim_is_active(claim):
                raise BrainCtlError(
                    "Another memory creation is still using this exact pointer file"
                )
            if (
                claim["brain_root"] != str(brain_root)
                or claim["owner"] != owner_name
                or claim["locale"] != locale
                or claim["preview_token"] != args.preview_token
            ):
                raise BrainCtlError(
                    "Interrupted pointer reservation does not match these exact creation details"
                )
        elif not isinstance(existing, dict):
            raise BrainCtlError(f"Invalid existing brain pointer: {pointer_path}")
        else:
            existing_root = resolve_path(existing.get("brain_root", ""))
            if existing_root == brain_root and state_path(brain_root).exists():
                state = load_state(brain_root)
                if existing.get("brain_id") != state.get("brain_id"):
                    raise BrainCtlError(
                        f"Pointer and brain identity do not match: {pointer_path}"
                    )
                output(
                    {
                        "ok": True,
                        "already_exists": True,
                        "brain_root": str(brain_root),
                        "brain_id": state["brain_id"],
                        "pointer_file": str(pointer_path),
                        "state": state["state"],
                    }
                )
                return
            raise BrainCtlError(
                f"Refusing to replace another brain pointer: {pointer_path}"
            )

    if claim is None:
        preview = doctor_report(
            brain_root,
            pointer_path=pointer_path,
            pointer_lexical_symlink=lexical_pointer_path.is_symlink(),
        )
        if not preview["ok"]:
            raise BrainCtlError("The approved memory location is no longer safe to use")
        if args.preview_token != preview.get("creation_preview_token"):
            raise BrainCtlError(
                "Memory creation preview no longer matches these exact locations; check them again"
            )
        if brain_root.exists() and (
            not brain_root.is_dir()
            or (not state_path(brain_root).exists() and any(brain_root.iterdir()))
        ):
            raise BrainCtlError(f"Target must be a new or empty directory: {brain_root}")
        if not brain_root.parent.exists() or not brain_root.parent.is_dir():
            raise BrainCtlError(
                f"Parent directory must already exist: {brain_root.parent}"
            )
        if not pointer_path.parent.exists() or not pointer_path.parent.is_dir():
            raise BrainCtlError(
                f"Pointer parent directory must already exist: {pointer_path.parent}"
            )
        claim = {
            "schema_version": 1,
            "status": "pending-creation",
            "claim_id": uuid.uuid4().hex,
            "created_at": utc_now(),
            "pid": os.getpid(),
            "host": platform.node() or "local-host-unknown",
            "brain_root": str(brain_root),
            "owner": owner_name,
            "locale": locale,
            "preview_token": args.preview_token,
        }
        _write_exclusive_private_json(pointer_path, claim)
        claim_created_here = True

    assert claim is not None
    claim_id = claim["claim_id"]
    if state_path(brain_root).exists():
        state = load_state(brain_root, allow_pending_pointer=True)
        if (
            resolve_path(state.get("builder_pointer", "")) != pointer_path
            or state.get("owner") != owner_name
            or state.get("locale") != locale
        ):
            raise BrainCtlError(
                "An existing memory at the target does not match this interrupted setup"
            )
        validation = validate_brain(brain_root)
        try:
            history = history_inspect(brain_root)
        except HistoryError as exc:
            raise BrainCtlError(
                f"Interrupted setup history could not be verified: {exc}"
            ) from exc
        if not validation["ok"] or not history["clean"]:
            raise BrainCtlError(
                "Interrupted setup target is not a clean valid memory"
            )
        pointer = {
            "schema_version": 1,
            "brain_id": state["brain_id"],
            "installation_id": state["installation_id"],
            "brain_root": str(brain_root),
        }
        _finalize_pointer_claim(
            pointer_path,
            claim_id=claim_id,
            pointer=pointer,
        )
        output(
            {
                "ok": True,
                "recovered_interrupted_setup": True,
                "brain_root": str(brain_root),
                "brain_id": state["brain_id"],
                "pointer_file": str(pointer_path),
                "state": state["state"],
                "revision": state["expected_revision"],
            }
        )
        return
    if brain_root.exists() and (not brain_root.is_dir() or any(brain_root.iterdir())):
        if claim_created_here:
            _remove_pointer_claim_if_exact(pointer_path, claim_id=claim_id)
        raise BrainCtlError(f"Target must be a new or empty directory: {brain_root}")
    if not brain_root.parent.exists() or not brain_root.parent.is_dir():
        if claim_created_here:
            _remove_pointer_claim_if_exact(pointer_path, claim_id=claim_id)
        raise BrainCtlError(
            f"Parent directory must already exist: {brain_root.parent}"
        )
    if not pointer_path.parent.exists() or not pointer_path.parent.is_dir():
        if claim_created_here:
            _remove_pointer_claim_if_exact(pointer_path, claim_id=claim_id)
        raise BrainCtlError(
            f"Pointer parent directory must already exist: {pointer_path.parent}"
        )

    target_was_empty = brain_root.exists()
    target_original_mode = (
        brain_root.stat().st_mode & 0o777 if target_was_empty else 0o700
    )
    try:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{brain_root.name}.personal-brain-setup-",
                dir=brain_root.parent,
            )
        )
    except Exception:
        if claim_created_here:
            _remove_pointer_claim_if_exact(pointer_path, claim_id=claim_id)
        raise
    state: dict[str, Any]
    target_published = False
    try:
        shutil.copytree(TEMPLATE_ROOT, staging, dirs_exist_ok=True)
        os.chmod(staging, 0o700)
        for rel in REQUIRED_DIRS:
            (staging / rel).mkdir(parents=True, exist_ok=True)
        runtime_root = staging / "system" / "runtime"
        shutil.copy2(
            POINTER_TEMPLATE,
            runtime_root / "assets" / "runner-pointer.md",
        )
        for guide_name in ("maintenance.md", "safety-rules.md"):
            shutil.copy2(
                SKILL_ROOT / "references" / guide_name,
                runtime_root / "guides" / guide_name,
            )
        replacements = {
            "OWNER_NAME": owner_name,
            "LOCALE": locale,
            "CREATED_DATE": today(),
            "BRAIN_ROOT": str(brain_root),
        }
        substitute_template_tree(staging, replacements)
        # Copy executable sources after template substitution. Their literal
        # placeholder strings are validation rules, not template fields.
        for script_name in (
            "brainctl.py",
            "state_control.py",
            "card_engine.py",
            "portable_history.py",
        ):
            shutil.copy2(
                SKILL_ROOT / "scripts" / script_name,
                runtime_root / "scripts" / script_name,
            )

        installation_id = str(uuid.uuid4())
        state = {
            "schema_version": 1,
            "brain_id": installation_id,
            "installation_id": installation_id,
            "brain_root": str(brain_root),
            "owner": owner_name,
            "locale": locale,
            "owner_display_name": owner_name,
            "user_language": locale,
            "builder_pointer": str(pointer_path),
            "state": "MAP_PERMISSION",
            "substate": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "entered_at": utc_now(),
            "blocking_issue": None,
            "compatibility": {},
            "manual_steps": [],
            "onboarding": None,
            "event_seq": 1,
            "events": [],
            "consents": [
                {
                    "kind": "memory-location",
                    "decision": "approved",
                    "scope": list(normalize_scopes([brain_root])),
                    "note": args.approval_note,
                    "at": utc_now(),
                }
            ],
            "scope": [],
            "areas": {},
            "session_history": {},
            "open_conflicts": [],
            "connections": {},
            "checks": {},
            "expected_revision": None,
            "authorized_write_set": [],
            "next_action": (
                "Persist the confirmed introduction, processing explanation, "
                "profile seed, and safety choices before mapping any folder."
            ),
        }
        created_event = {
            "seq": 1,
            "at": state["created_at"],
            "event": "brain-created",
        }
        state["events"] = [created_event]
        atomic_write_json(state_path(staging), state)
        atomic_write_text(
            events_path(staging),
            json.dumps(created_event, ensure_ascii=False, sort_keys=True) + "\n",
        )

        validation = validate_brain(staging, validate_operating_state=False)
        if not validation["ok"]:
            raise BrainCtlError(
                "Generated memory template failed validation: "
                + "; ".join(validation["errors"])
            )
        revision = history_initialize(
            staging,
            "Create the private memory library",
            "The owner approved creation in this exact location",
        )
        state["expected_revision"] = revision
        atomic_write_json(state_path(staging), state)
        if target_was_empty:
            brain_root.rmdir()
        os.replace(staging, brain_root)
        target_published = True
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if target_was_empty and not brain_root.exists():
            brain_root.mkdir(mode=target_original_mode)
        if claim_created_here and not target_published:
            _remove_pointer_claim_if_exact(pointer_path, claim_id=claim_id)
        raise

    pointer = {
        "schema_version": 1,
        "brain_id": state["brain_id"],
        "installation_id": state["installation_id"],
        "brain_root": str(brain_root),
    }
    _finalize_pointer_claim(
        pointer_path,
        claim_id=claim_id,
        pointer=pointer,
    )
    output(
        {
            "ok": True,
            "brain_root": str(brain_root),
            "brain_id": state["brain_id"],
            "pointer_file": str(pointer_path),
            "state": state["state"],
            "revision": state["expected_revision"],
        }
    )


def cmd_status(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    missing_dirs = [rel for rel in REQUIRED_DIRS if not (brain_root / rel).is_dir()]
    pending_transactions = [
        path.name for path, _item in pending_connection_transactions(brain_root)
    ]
    try:
        history = history_inspect(brain_root)
        history_error = None
    except HistoryError as exc:
        history = {
            "integrity": "failed",
            "head": None,
            "clean": False,
            "added": [],
            "modified": [],
            "deleted": [],
        }
        history_error = str(exc)
    history_clean = bool(history["clean"])
    revision_matches = history["head"] == state.get("expected_revision")
    validation = validate_brain(brain_root)
    pointer_raw = state.get("builder_pointer")
    builder_pointer_status = (
        "verified"
        if isinstance(pointer_raw, str) and Path(pointer_raw).exists()
        else "missing-manual-memory-selection-required"
    )
    status = {
        "ok": (
            not missing_dirs
            and history["integrity"] == "ok"
            and history_clean
            and revision_matches
            and not pending_transactions
            and validation["ok"]
        ),
        "memory_ok": validation["ok"],
        "brain_id": state["brain_id"],
        "owner": state["owner"],
        "brain_root": str(brain_root),
        "state": state["state"],
        "onboarding_complete": isinstance(state.get("onboarding"), dict),
        "next_action": state.get("next_action"),
        "scope": state.get("scope", []),
        "areas": state.get("areas", {}),
        "connections": state.get("connections", {}),
        "missing_directories": missing_dirs,
        "history_clean": history_clean,
        "history": history,
        "history_error": history_error,
        "revision_matches_state": revision_matches,
        "pending_connection_transactions": pending_transactions,
        "builder_pointer_status": builder_pointer_status,
        "validation_errors": validation["errors"],
        "validation_warnings": validation["warnings"],
    }
    output(status)
    if not status["ok"]:
        raise SystemExit(1)


def cmd_state(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    if args.show:
        output({"ok": True, "state": state})
        return
    ensure_record_text_is_safe(
        {
            "next_action": args.next_action or "",
            "blocking_issue": args.blocking_issue or "",
        }
    )
    previous = state["state"]
    if (
        args.stage == "READY"
        or
        previous == "CORE_REVIEW"
        and args.stage == "LIBRARY_VALIDATION"
    ) or (
        previous == "LIBRARY_VALIDATION"
        and args.stage == "CONNECTION_REVIEW"
    ) or (
        previous == "HANDOFF"
        and args.stage == "READY"
    ):
        validation = validate_brain(brain_root)
        if not validation["ok"]:
            raise BrainCtlError(
                "Complete memory validation must pass before this transition: "
                + "; ".join(validation["errors"])
            )
        try:
            history = history_inspect(brain_root)
        except HistoryError as exc:
            raise BrainCtlError(
                f"Local history must be healthy before this transition: {exc}"
            ) from exc
        if not history["clean"]:
            raise BrainCtlError(
                "All intended memory changes must be saved before this transition"
            )
    try:
        transitioned = transition_state(
            state,
            args.stage,
            brain_root,
            recovery=args.recovery,
            blocking_issue=args.blocking_issue,
            next_action=args.next_action,
            required_check_ids=REQUIRED_INSTALLATION_CHECKS,
        )
    except (StateTransitionError, StateValidationError, ValueError) as exc:
        raise BrainCtlError(str(exc)) from exc
    save_state(
        brain_root,
        transitioned,
        "state-changed",
        {
            "from": previous,
            "to": args.stage,
            "next_action": transitioned.get("next_action"),
        },
    )
    output(
        {
            "ok": True,
            "from": previous,
            "to": args.stage,
            "next_action": transitioned.get("next_action"),
        }
    )


def ensure_record_text_is_safe(payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) > 20_000:
        raise BrainCtlError("Record is too large for setup state")
    if contains_secret_value_text(text):
        raise BrainCtlError("Refusing to store a secret-like value in setup state")


def core_profile_fingerprint(brain_root: Path) -> str:
    path = brain_root / "profile" / "owner.md"
    try:
        item = path.lstat()
    except FileNotFoundError as exc:
        raise BrainCtlError("The compact owner profile is missing") from exc
    if (
        not statlib.S_ISREG(item.st_mode)
        or item.st_nlink != 1
        or path.is_symlink()
    ):
        raise BrainCtlError("The compact owner profile must be one regular file")
    try:
        raw = read_regular_bytes(
            path,
            max_size=2_000_000,
            description="compact owner profile",
        )
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BrainCtlError(f"Cannot read the compact owner profile: {exc}") from exc
    unresolved = [
        placeholder for placeholder in CORE_PROFILE_PLACEHOLDERS if placeholder in text
    ]
    if unresolved:
        raise BrainCtlError(
            "The compact owner profile still contains onboarding placeholders"
        )
    if len(text.strip()) < 200:
        raise BrainCtlError("The compact owner profile is unexpectedly incomplete")
    return hashlib.sha256(raw).hexdigest()


def cmd_record(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    at = utc_now()

    if args.record_kind == "onboarding":
        if state["state"] != "MAP_PERMISSION":
            raise BrainCtlError(
                "Confirmed onboarding choices can be recorded only before folder mapping"
            )
        if not args.introduction_confirmed:
            raise BrainCtlError(
                "Onboarding cannot be recorded before the person confirms the introduction"
            )
        if not args.processing_acknowledged:
            raise BrainCtlError(
                "Onboarding cannot be recorded before processing is explained and acknowledged"
            )

        def cleaned_items(values: list[str] | None, label: str) -> list[str]:
            result: list[str] = []
            for raw in values or []:
                value = raw.strip()
                if not value:
                    raise BrainCtlError(f"{label} entries must not be empty")
                if len(value) > 500:
                    raise BrainCtlError(f"{label} entries must be at most 500 characters")
                if value not in result:
                    result.append(value)
            if len(result) > 100:
                raise BrainCtlError(f"{label} may contain at most 100 entries")
            return result

        work_summary = None if args.withhold_work_summary else args.work_summary.strip()
        if work_summary is not None and not work_summary:
            raise BrainCtlError("Saved work summary must not be empty")
        if work_summary is not None and len(work_summary) > 2_000:
            raise BrainCtlError("Work summary must be at most 2000 characters")
        desired_outcome = args.desired_outcome.strip()
        processing_note = args.processing_note.strip()
        if not desired_outcome or len(desired_outcome) > 2_000:
            raise BrainCtlError(
                "Desired outcome must contain 1 to 2000 characters"
            )
        if not processing_note or len(processing_note) > 2_000:
            raise BrainCtlError(
                "Processing explanation must contain 1 to 2000 characters"
            )
        manual_steps = cleaned_items(args.manual_steps, "Manual step")
        entry = {
            "schema_version": 1,
            "recorded_at": at,
            "introduction_confirmed": True,
            "work_summary_saved": work_summary is not None,
            "work_summary": work_summary,
            "desired_outcome": desired_outcome,
            "computer_use": args.computer_use,
            "never_open": cleaned_items(args.never_open, "Never-open choice"),
            "read_not_store": cleaned_items(
                args.read_not_store,
                "Read-but-do-not-store choice",
            ),
            "processing": {
                "mode": args.processing_mode,
                "note": processing_note,
                "acknowledged": True,
            },
            "compatibility": {
                "classification": args.capability,
                "manual_steps": manual_steps,
            },
        }
        ensure_record_text_is_safe(entry)
        state["onboarding"] = entry
        state["compatibility"] = dict(entry["compatibility"])
        state["manual_steps"] = list(manual_steps)
        state["next_action"] = (
            "Explain and request approval for exact folders to map by name."
        )
        save_state(
            brain_root,
            state,
            "onboarding-recorded",
            {
                "work_summary_saved": entry["work_summary_saved"],
                "never_open_count": len(entry["never_open"]),
                "read_not_store_count": len(entry["read_not_store"]),
                "processing_mode": args.processing_mode,
                "capability": args.capability,
                "manual_step_count": len(manual_steps),
            },
        )
        output(
            {
                "ok": True,
                "onboarding_complete": True,
                "work_summary_saved": entry["work_summary_saved"],
                "never_open_count": len(entry["never_open"]),
                "read_not_store_count": len(entry["read_not_store"]),
                "processing_mode": args.processing_mode,
                "capability": args.capability,
                "manual_step_count": len(manual_steps),
            }
        )
        return

    if args.record_kind == "consent":
        approval_stages = {
            "metadata-map": {"MAP_PERMISSION", "METADATA_MAP", "SCOPE_REVIEW"},
            "content-audit": {"DEEP_AUDIT_GATE", "CONTOUR_LOOP"},
            "session-history": {
                "SESSIONS_PERMISSION",
                "SESSION_HISTORY_AUDIT",
            },
            "session-history-metadata": {
                "SESSIONS_PERMISSION",
                "SESSION_HISTORY_AUDIT",
            },
            "connection": {"CONNECTION_REVIEW", "CONNECTION_APPLY"},
            "connection-read": {"CONNECTION_REVIEW", "CONNECTION_APPLY"},
            "recovery-copy": {"RECOVERY_COPY_REVIEW"},
            "publication": {"HANDOFF", "READY"},
        }
        allowed = approval_stages.get(args.kind)
        if (
            args.kind == "metadata-map"
            and args.decision == "approved"
            and not isinstance(state.get("onboarding"), dict)
        ):
            raise BrainCtlError(
                "Confirmed onboarding choices must be saved before folder mapping approval"
            )
        if (
            args.decision == "approved"
            and allowed is not None
            and state["state"] not in allowed
        ):
            raise BrainCtlError(
                f"{args.kind} approval is not available at the current guided stage"
            )
        entry = {
            "kind": args.kind,
            "decision": args.decision,
            "scope": list(normalize_scopes(args.scope)),
            "note": args.note or "",
            "at": at,
        }
        ensure_record_text_is_safe(entry)
        state.setdefault("consents", []).append(entry)
        save_state(brain_root, state, "consent-recorded", entry)
        output({"ok": True, "consent": entry})
        return

    if args.record_kind == "area":
        if state["state"] not in {"SCOPE_REVIEW", "CONTOUR_LOOP"}:
            raise BrainCtlError(
                "Area results can be recorded only during scope review or area review"
            )
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.id):
            raise BrainCtlError("Area id must use ASCII kebab-case")
        paths = [resolve_path(raw) for raw in args.paths]
        if len({str(path) for path in paths}) != len(paths):
            raise BrainCtlError("Area paths must be unique")
        coverage = args.coverage or args.status
        if not coverage:
            raise BrainCtlError("Area coverage is required")
        execution = args.execution or (
            "in-progress" if coverage == "needs-recheck" else "closed"
        )
        existing_area = state.setdefault("areas", {}).get(args.id, {})
        if coverage == "content-reviewed":
            if state["state"] != "CONTOUR_LOOP":
                raise BrainCtlError(
                    "Content-reviewed status is available only during area review"
                )
            for path in paths:
                ensure_approved(
                    state,
                    path,
                    {"content"},
                    operation="content",
                )
        elif coverage == "names-only":
            for path in paths:
                ensure_approved(
                    state,
                    path,
                    {"metadata", "content", "names-only"},
                    operation="metadata",
                )
        entry = {
            "id": args.id,
            "label": args.label,
            "paths": [str(path) for path in paths],
            "coverage": coverage,
            "execution": execution,
            "cursor": args.cursor if args.cursor is not None else existing_area.get("cursor"),
            "cards": sorted(
                set(
                    args.cards
                    if args.cards is not None
                    else existing_area.get("cards", [])
                )
            ),
            "attempt": args.attempt or existing_area.get("attempt", 0),
            "pre_fingerprints": dict(existing_area.get("pre_fingerprints", {})),
            "post_fingerprints": dict(existing_area.get("post_fingerprints", {})),
            "journal": (
                args.journal
                if args.journal is not None
                else existing_area.get("journal")
            ),
            "saved_revision": (
                args.saved_revision
                if args.saved_revision is not None
                else existing_area.get("saved_revision")
            ),
            "conflicts": sorted(
                set(
                    args.conflicts
                    if args.conflicts is not None
                    else existing_area.get("conflicts", [])
                )
            ),
            "note": args.note if args.note is not None else existing_area.get("note", ""),
            "updated_at": at,
        }
        if coverage == "content-reviewed":
            if not isinstance(entry.get("pre_fingerprints"), dict) or not entry[
                "pre_fingerprints"
            ].get("sha256"):
                raise BrainCtlError(
                    "Content-reviewed closure requires a pre-read source snapshot"
                )
            if (
                not isinstance(entry.get("post_fingerprints"), dict)
                or entry["post_fingerprints"].get("verified") is not True
            ):
                raise BrainCtlError(
                    "Content-reviewed closure requires a successful post-read source check"
                )
            if not entry.get("journal"):
                raise BrainCtlError(
                    "Content-reviewed closure requires a journal card path"
                )
            journal_path = Path(entry["journal"])
            if (
                journal_path.is_absolute()
                or ".." in journal_path.parts
                or not journal_path.as_posix().startswith("journal/")
                or journal_path.suffix != ".md"
                or not (brain_root / journal_path).is_file()
            ):
                raise BrainCtlError(
                    "Area journal must name an existing brain-relative journal/*.md card"
                )
            if not entry.get("saved_revision"):
                raise BrainCtlError(
                    "Content-reviewed closure requires its saved memory revision"
                )
            if entry["saved_revision"] != state.get("expected_revision"):
                raise BrainCtlError(
                    "Area saved revision must match the current memory revision"
                )
            exact_paths = [str(path) for path in paths]
            if existing_area.get("paths") != exact_paths:
                raise BrainCtlError(
                    "Content-reviewed closure paths must exactly match the "
                    "roots captured by this area's source snapshot"
                )
            if not isinstance(entry.get("attempt"), int) or entry["attempt"] < 1:
                raise BrainCtlError(
                    "Content-reviewed closure requires a positive snapshot attempt"
                )

            def bound_snapshot(
                fingerprint: dict[str, Any],
                phase: str,
            ) -> dict[str, Any]:
                raw_snapshot = fingerprint.get("snapshot")
                if not isinstance(raw_snapshot, str):
                    raise BrainCtlError(
                        f"Content-reviewed closure lacks its {phase} snapshot path"
                    )
                snapshot_path = resolve_path(raw_snapshot)
                expected_snapshot = (
                    brain_root
                    / "work"
                    / "setup"
                    / "snapshots"
                    / f"{args.id}-attempt-{entry['attempt']}-{phase}.json"
                ).resolve()
                if snapshot_path != expected_snapshot:
                    raise BrainCtlError(
                        f"Area {phase} snapshot is not the exact private snapshot "
                        "for this id and attempt"
                    )
                payload = read_json(snapshot_path)
                payload_roots = [
                    str(resolve_path(item.get("path", "")))
                    for item in payload.get("roots", [])
                    if isinstance(item, dict)
                ]
                if (
                    payload.get("brain_id") != state["brain_id"]
                    or payload.get("label") != args.id
                    or payload.get("attempt") != entry["attempt"]
                    or payload_roots != exact_paths
                    or fingerprint.get("roots") != exact_paths
                ):
                    raise BrainCtlError(
                        f"Area {phase} snapshot does not match its exact roots"
                    )
                actual_fingerprint = hashlib.sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if fingerprint.get("sha256") != actual_fingerprint:
                    raise BrainCtlError(
                        f"Area {phase} snapshot fingerprint no longer matches"
                    )
                return payload

            pre_payload = bound_snapshot(entry["pre_fingerprints"], "pre")
            post_payload = bound_snapshot(entry["post_fingerprints"], "post")
            if not snapshot_diff(pre_payload, post_payload)["pass"]:
                raise BrainCtlError(
                    "Content-reviewed closure requires unchanged source snapshots"
                )
        ensure_record_text_is_safe(entry)
        state["areas"][args.id] = entry
        save_state(brain_root, state, "area-recorded", entry)
        output({"ok": True, "area": {**entry, "status": coverage}})
        return

    if args.record_kind == "check":
        if state["state"] not in {
            "ACCEPTANCE_TESTING",
            "RECOVERY_COPY_REVIEW",
        }:
            raise BrainCtlError(
                "Checks can be recorded only during acceptance or recovery review"
            )
        if (
            state["state"] == "RECOVERY_COPY_REVIEW"
            and args.id != "recovery-restore"
        ):
            raise BrainCtlError(
                "Only the recovery-restore check belongs in recovery review"
            )
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.id):
            raise BrainCtlError("Check id must use ASCII kebab-case")
        entry = {
            "id": args.id,
            "status": args.status,
            "evidence": args.evidence,
            "checked_at": at,
        }
        ensure_record_text_is_safe(entry)
        state.setdefault("checks", {})[args.id] = entry
        save_state(brain_root, state, "acceptance-check-recorded", entry)
        output({"ok": True, "check": entry})
        return

    if args.record_kind == "core":
        if state["state"] != "CORE_REVIEW":
            raise BrainCtlError(
                "The compact owner profile can be confirmed only during core review"
            )
        fingerprint = core_profile_fingerprint(brain_root)
        try:
            history = history_inspect(brain_root)
        except HistoryError as exc:
            raise BrainCtlError(
                f"Local history must be healthy before profile confirmation: {exc}"
            ) from exc
        if (
            not history["clean"]
            or history["head"] != state.get("expected_revision")
        ):
            raise BrainCtlError(
                "Save all profile changes before confirming the compact owner profile"
            )
        entry = {
            "confirmed_at": at,
            "profile_sha256": fingerprint,
            "note": args.note,
        }
        ensure_record_text_is_safe(entry)
        state["core_review"] = entry
        save_state(brain_root, state, "core-profile-confirmed", entry)
        output({"ok": True, "core_review": entry})
        return

    if args.record_kind == "session":
        if state["state"] not in {
            "SESSIONS_PERMISSION",
            "SESSION_HISTORY_AUDIT",
        }:
            raise BrainCtlError(
                "Session sources can be recorded only during session-history review"
            )
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.id):
            raise BrainCtlError("Session source id must use ASCII kebab-case")
        for field_name, value in (("date-from", args.date_from), ("date-to", args.date_to)):
            if value and not is_iso_date(value):
                raise BrainCtlError(f"{field_name} must use YYYY-MM-DD")
        resolved_session_path = resolve_path(args.path) if args.path else None
        content_coverages = {
            "distillates-reviewed",
            "transcripts-reviewed",
            "needs-recheck",
        }
        if (
            state["state"] == "SESSIONS_PERMISSION"
            and args.coverage in content_coverages
        ):
            raise BrainCtlError(
                "Session content coverage requires the separate content-review stage"
            )
        if args.coverage in content_coverages and resolved_session_path is None:
            raise BrainCtlError(
                "Session content coverage requires one exact approved history location"
            )
        if resolved_session_path is not None and args.coverage not in {
            "excluded-by-user",
            "unavailable",
            "blocked-sensitive",
        }:
            required_consent = (
                "session-history-metadata"
                if args.coverage in {"pending", "metadata-only"}
                else "session-history"
            )
            if not has_active_consent(
                state,
                required_consent,
                [resolved_session_path],
            ):
                raise BrainCtlError(
                    f"Exact active {required_consent} consent is required for {resolved_session_path}"
                )
        entry = {
            "id": args.id,
            "program": args.program,
            "path": str(resolved_session_path) if resolved_session_path else None,
            "date_from": args.date_from,
            "date_to": args.date_to,
            "execution": args.execution,
            "coverage": args.coverage,
            "cursor": args.cursor or "",
            "exclusions": sorted(set(args.exclusions or [])),
            "failures": sorted(set(args.failures or [])),
            "note": args.note or "",
            "updated_at": at,
        }
        existing_session = state.setdefault("session_history", {}).get(args.id)
        if isinstance(existing_session, dict):
            immutable_identity = ("program", "path", "date_from", "date_to")
            changed_identity = [
                field
                for field in immutable_identity
                if existing_session.get(field) != entry.get(field)
            ]
            if changed_identity:
                raise BrainCtlError(
                    "A recorded session source id cannot be rebound; "
                    "use a new id when program, location, or date range changes "
                    f"({', '.join(changed_identity)})"
                )
        ensure_record_text_is_safe(entry)
        state["session_history"][args.id] = entry
        save_state(brain_root, state, "session-source-recorded", entry)
        output({"ok": True, "session_source": entry})
        return

    if args.record_kind == "connection":
        if state["state"] not in {
            "CONNECTION_REVIEW",
            "CONNECTION_APPLY",
            "ACCEPTANCE_TESTING",
        }:
            raise BrainCtlError(
                "Connection status can be recorded only during connection or acceptance review"
            )
        target = resolve_path(args.target)
        key = str(target)
        existing = state.setdefault("connections", {}).get(key)
        if args.status == "verified" and not (args.evidence or "").strip():
            raise BrainCtlError(
                "A verified connection requires evidence from an actual fresh conversation"
            )
        if args.status in {"connected", "verified", "failed-closed"} and not existing:
            raise BrainCtlError(
                "Connection target must first be installed or recorded as pending"
            )
        entry = dict(existing or {})
        entry.update(
            {
                "target": key,
                "status": args.status,
                "selected": args.selected == "yes",
                "evidence": args.evidence or "",
                "updated_at": at,
            }
        )
        ensure_record_text_is_safe(entry)
        state["connections"][key] = entry
        save_state(brain_root, state, "connection-status-recorded", entry)
        output({"ok": True, "connection": entry})
        return

    raise BrainCtlError(f"Unknown record kind: {args.record_kind}")


def cmd_scope(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    if (
        args.mode in {"metadata", "names-only", "content"}
        and not isinstance(state.get("onboarding"), dict)
    ):
        raise BrainCtlError(
            "Confirmed onboarding choices must be saved before folder permissions"
        )
    path = resolve_path(args.path)
    assert_not_broad_path(path, purpose="scope approval")
    if not path.exists() or not path.is_dir():
        raise BrainCtlError(f"Scope path must be an existing directory: {path}")
    if is_within(brain_root, path) or is_within(path, brain_root):
        raise BrainCtlError("Source scope and brain directory must be separate")
    if args.mode in {"metadata", "names-only"}:
        allowed_stages = {"MAP_PERMISSION", "METADATA_MAP", "SCOPE_REVIEW"}
    elif args.mode == "content":
        allowed_stages = {"DEEP_AUDIT_GATE", "CONTOUR_LOOP"}
    else:
        allowed_stages = {
            "MAP_PERMISSION",
            "METADATA_MAP",
            "SCOPE_REVIEW",
            "DEEP_AUDIT_GATE",
            "CONTOUR_LOOP",
        }
    if state["state"] not in allowed_stages:
        raise BrainCtlError(
            "This folder permission is not available at the current guided stage"
        )
    consent_kind = (
        "content-audit"
        if args.mode == "content"
        else "metadata-map"
        if args.mode in {"metadata", "names-only"}
        else None
    )
    if consent_kind and not has_active_consent(state, consent_kind, [path]):
        raise BrainCtlError(
            f"Exact active {consent_kind} consent is required for {path}"
        )
    item = {
        "path": str(path),
        "mode": args.mode,
        "label": args.label or path.name,
        "reason": args.reason or "",
        "approved_at": utc_now(),
        "consent_scope": [str(path)] if consent_kind else [],
    }
    ensure_record_text_is_safe(item)
    scope = [entry for entry in state.get("scope", []) if resolve_path(entry["path"]) != path]
    scope.append(item)
    state["scope"] = sorted(scope, key=lambda entry: entry["path"])
    save_state(brain_root, state, "scope-updated", item)
    output({"ok": True, "scope": state["scope"]})


def walk_metadata(
    root: Path,
    patterns: list[str],
    *,
    include_directories: bool = True,
    include_hashes: bool = False,
    excluded_roots: Iterable[Path] = (),
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    entries: list[dict[str, Any]] = []
    stats = {
        "files": 0,
        "directories": 0,
        "symlinks_skipped": 0,
        "mounts_skipped": 0,
        "sensitive_skipped": 0,
        "excluded_skipped": 0,
        "unreadable_skipped": 0,
    }
    excluded = tuple(excluded_roots)
    try:
        root_device = root.stat().st_dev
    except OSError as exc:
        raise BrainCtlError(f"Cannot inspect approved root metadata: {root}") from exc

    def is_explicitly_excluded(path: Path) -> bool:
        resolved = path.resolve()
        return any(is_within(resolved, denied) for denied in excluded)

    for current_raw, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_raw)
        try:
            relative_current = current.relative_to(root)
        except ValueError:
            continue
        kept_dirs: list[str] = []
        for name in sorted(dir_names):
            path = current / name
            rel = relative_current / name
            if path.is_symlink():
                stats["symlinks_skipped"] += 1
                continue
            try:
                resolved_directory = path.resolve(strict=True)
            except OSError:
                stats["unreadable_skipped"] += 1
                continue
            if not is_within(resolved_directory, root):
                stats["symlinks_skipped"] += 1
                continue
            try:
                directory_stat = path.stat()
            except OSError:
                stats["unreadable_skipped"] += 1
                continue
            if directory_stat.st_dev != root_device or os.path.ismount(path):
                stats["mounts_skipped"] += 1
                continue
            if is_explicitly_excluded(path):
                stats["excluded_skipped"] += 1
                continue
            if sensitive_category(path, rel, patterns):
                stats["sensitive_skipped"] += 1
                continue
            kept_dirs.append(name)
            if include_directories:
                try:
                    stat_result = directory_stat
                except OSError:
                    stats["unreadable_skipped"] += 1
                    continue
                entries.append(
                    {
                        "kind": "directory",
                        "path": rel.as_posix(),
                        "mtime_ns": stat_result.st_mtime_ns,
                        "ctime_ns": getattr(
                            stat_result,
                            "st_ctime_ns",
                            int(stat_result.st_ctime * 1_000_000_000),
                        ),
                        "device": stat_result.st_dev,
                        "inode": stat_result.st_ino,
                        "mode": stat_result.st_mode & 0o777,
                    }
                )
                stats["directories"] += 1
        dir_names[:] = kept_dirs
        for name in sorted(file_names):
            path = current / name
            rel = relative_current / name
            if path.is_symlink():
                stats["symlinks_skipped"] += 1
                continue
            if is_explicitly_excluded(path):
                stats["excluded_skipped"] += 1
                continue
            if sensitive_category(path, rel, patterns):
                stats["sensitive_skipped"] += 1
                continue
            try:
                resolved_before = path.resolve(strict=True)
                if not is_within(resolved_before, root):
                    stats["symlinks_skipped"] += 1
                    continue
                stat_result = path.lstat()
            except OSError:
                stats["unreadable_skipped"] += 1
                continue
            if not statlib.S_ISREG(stat_result.st_mode):
                stats["unreadable_skipped"] += 1
                continue
            if stat_result.st_dev != root_device or os.path.ismount(path):
                stats["mounts_skipped"] += 1
                continue
            content_hash: str | None = None
            if include_hashes:
                try:
                    content_hash = hash_regular_file_without_following(
                        path,
                        stat_result,
                    )
                except (OSError, BrainCtlError):
                    stats["unreadable_skipped"] += 1
                    continue
            try:
                resolved_after = path.resolve(strict=True)
            except OSError:
                stats["unreadable_skipped"] += 1
                continue
            if (
                resolved_after != resolved_before
                or not is_within(resolved_after, root)
            ):
                stats["unreadable_skipped"] += 1
                continue
            entries.append(
                {
                    "kind": "file",
                    "path": rel.as_posix(),
                    "suffix": path.suffix.casefold(),
                    "size": stat_result.st_size,
                    "mtime_ns": stat_result.st_mtime_ns,
                    "ctime_ns": getattr(
                        stat_result,
                        "st_ctime_ns",
                        int(stat_result.st_ctime * 1_000_000_000),
                    ),
                    "device": stat_result.st_dev,
                    "inode": stat_result.st_ino,
                    "mode": stat_result.st_mode & 0o777,
                    **({"sha256": content_hash} if content_hash is not None else {}),
                }
            )
            stats["files"] += 1
    return entries, stats


def hash_regular_file_without_following(
    path: Path,
    expected: os.stat_result,
) -> str:
    if statlib.S_ISLNK(expected.st_mode) or not statlib.S_ISREG(expected.st_mode):
        raise BrainCtlError(f"Refusing to fingerprint a non-regular file: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not statlib.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino)
            != (expected.st_dev, expected.st_ino)
        ):
            raise BrainCtlError(f"Source file changed identity while opening: {path}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        before_signature = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            getattr(
                opened,
                "st_mtime_ns",
                int(opened.st_mtime * 1_000_000_000),
            ),
        )
        after_signature = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            getattr(
                after,
                "st_mtime_ns",
                int(after.st_mtime * 1_000_000_000),
            ),
        )
        if before_signature != after_signature:
            raise BrainCtlError(f"Source file changed while fingerprinting: {path}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def cmd_inventory(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    if state["state"] != "METADATA_MAP":
        raise BrainCtlError(
            "The names-only map is available only at its guided mapping stage"
        )
    patterns = load_ignore_patterns(brain_root)
    excluded_roots = [
        resolve_path(item["path"])
        for item in state.get("scope", [])
        if item.get("mode") == "exclude"
    ]
    setup = brain_root / "work" / "setup"
    registry_path = setup / "source-roots.json"
    registry: dict[str, str] = {}
    if registry_path.exists() or registry_path.is_symlink():
        existing_registry = read_json(registry_path)
        if not isinstance(existing_registry, dict):
            raise BrainCtlError("Source-root registry must be a mapping")
        for alias, raw_path in existing_registry.items():
            if (
                not isinstance(alias, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", alias)
                or not isinstance(raw_path, str)
            ):
                raise BrainCtlError("Source-root registry contains an invalid entry")
            canonical = str(resolve_path(raw_path))
            if raw_path != canonical:
                raise BrainCtlError(
                    f"Source-root registry path is not canonical for {alias}"
                )
            registry[alias] = canonical
    aliases_by_path = {path: alias for alias, path in registry.items()}
    next_alias_number = 1

    def allocate_alias() -> str:
        nonlocal next_alias_number
        while f"root-{next_alias_number}" in registry:
            next_alias_number += 1
        alias = f"root-{next_alias_number}"
        next_alias_number += 1
        return alias

    roots: list[tuple[str, Path]] = []
    selected_paths: set[str] = set()
    for raw in args.roots:
        path = resolve_path(raw)
        ensure_approved(
            state,
            path,
            {"metadata", "content", "names-only"},
            operation="metadata",
        )
        canonical = str(path)
        if canonical in selected_paths:
            raise BrainCtlError(f"Duplicate inventory root: {path}")
        selected_paths.add(canonical)
        alias = aliases_by_path.get(canonical)
        if alias is None:
            alias = allocate_alias()
            registry[alias] = canonical
            aliases_by_path[canonical] = alias
        roots.append((alias, path))

    all_entries: list[dict[str, Any]] = []
    totals = {
        "files": 0,
        "directories": 0,
        "symlinks_skipped": 0,
        "mounts_skipped": 0,
        "sensitive_skipped": 0,
        "excluded_skipped": 0,
        "unreadable_skipped": 0,
    }
    selected_root_map: dict[str, str] = {}
    for alias, root in roots:
        entries, stats = walk_metadata(
            root,
            patterns,
            excluded_roots=excluded_roots,
        )
        selected_root_map[alias] = str(root)
        for entry in entries:
            entry["root"] = alias
            all_entries.append(entry)
        for key, value in stats.items():
            totals[key] += value

    atomic_write_json(registry_path, registry)
    jsonl = "".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        for entry in all_entries
    )
    atomic_write_text(setup / "inventory.jsonl", jsonl)
    summary_lines = [
        "# Names-only inventory",
        "",
        f"Created: {utc_now()}",
        "",
        "## Approved roots",
        "",
    ]
    summary_lines.extend(f"- `{alias}` — `{root}`" for alias, root in roots)
    summary_lines.extend(
        [
            "",
            "## Counts",
            "",
            *(f"- {key}: {value}" for key, value in totals.items()),
            "",
            "No file contents were opened by this inventory command.",
            "",
        ]
    )
    atomic_write_text(setup / "inventory-summary.md", "\n".join(summary_lines))
    state = transition_state(
        state,
        "SCOPE_REVIEW",
        brain_root,
        next_action="Review and classify discovered work areas with the owner.",
    )
    state["inventory"] = {
        "created_at": utc_now(),
        "roots": list(selected_root_map),
        **totals,
    }
    save_state(brain_root, state, "inventory-complete", state["inventory"])
    output(
        {
            "ok": True,
            "entries": len(all_entries),
            "counts": totals,
            "roots": selected_root_map,
        }
    )


def snapshot_payload(
    roots: list[Path],
    patterns: list[str],
    *,
    brain_id: str,
    label: str,
    attempt: int,
    excluded_roots: Iterable[Path] = (),
) -> dict[str, Any]:
    all_entries: list[dict[str, Any]] = []
    root_records: list[dict[str, Any]] = []
    total_stats = {
        "files": 0,
        "directories": 0,
        "symlinks_skipped": 0,
        "mounts_skipped": 0,
        "sensitive_skipped": 0,
        "excluded_skipped": 0,
        "unreadable_skipped": 0,
    }
    for index, root in enumerate(roots, start=1):
        alias = f"root-{index}"
        entries, stats = walk_metadata(
            root,
            patterns,
            # A source fingerprint must never open ordinary file content:
            # approved material may still contain an undiscovered secret.
            include_hashes=False,
            excluded_roots=excluded_roots,
        )
        for entry in entries:
            entry["root"] = alias
            all_entries.append(entry)
        for key, value in stats.items():
            total_stats[key] += value
        root_records.append({"alias": alias, "path": str(root)})
    return {
        "schema_version": 1,
        "brain_id": brain_id,
        "label": label,
        "attempt": attempt,
        "created_at": utc_now(),
        "roots": root_records,
        "entries": all_entries,
        "stats": total_stats,
    }


def safe_label(label: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", label.casefold()).strip("-")
    if not value:
        raise BrainCtlError("Snapshot label must contain letters or digits")
    return value


def snapshot_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    def index(payload: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
        return {
            (entry.get("root", "root-1"), entry["kind"], entry["path"]): entry
            for entry in payload.get("entries", [])
        }

    old = index(before)
    new = index(after)
    root_count = len(before.get("roots", []))

    def display(key: tuple[str, str, str]) -> str:
        return key[2] if root_count <= 1 else f"{key[0]}:{key[2]}"

    added = sorted(display(key) for key in new.keys() - old.keys())
    removed = sorted(display(key) for key in old.keys() - new.keys())
    changed = sorted(
        display(key)
        for key in old.keys() & new.keys()
        if old[key] != new[key]
    )
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "pass": not (added or removed or changed),
    }


def cmd_snapshot(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    if state["state"] != "CONTOUR_LOOP":
        raise BrainCtlError(
            "Source snapshots are available only while reviewing one approved area"
        )
    patterns = load_ignore_patterns(brain_root)
    excluded_roots = [
        resolve_path(item["path"])
        for item in state.get("scope", [])
        if item.get("mode") == "exclude"
    ]
    if args.attempt < 1:
        raise BrainCtlError("Snapshot attempt must be a positive integer")
    roots = [resolve_path(raw) for raw in args.root]
    for root in roots:
        ensure_approved(
            state,
            root,
            {"content"},
            operation="content",
        )
    label = safe_label(args.label)
    ensure_record_text_is_safe({"snapshot_label": args.label})
    snapshot_dir = brain_root / "work" / "setup" / "snapshots"
    before_path = snapshot_dir / f"{label}-attempt-{args.attempt}-pre.json"
    if args.action == "create":
        already_exists = before_path.exists()
        if already_exists:
            payload = read_json(before_path)
            recorded_roots = [
                resolve_path(item["path"]) for item in payload.get("roots", [])
            ]
            if (
                payload.get("brain_id") != state["brain_id"]
                or payload.get("label") != label
                or payload.get("attempt") != args.attempt
                or recorded_roots != roots
            ):
                raise BrainCtlError(
                    "Existing snapshot does not match this memory, area, attempt, or root set"
                )
        else:
            payload = snapshot_payload(
                roots,
                patterns,
                brain_id=state["brain_id"],
                label=label,
                attempt=args.attempt,
                excluded_roots=excluded_roots,
            )
            atomic_write_json(before_path, payload)
        fingerprint = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = state.setdefault("areas", {}).get(label, {})
        if (
            already_exists
            and existing.get("attempt") == args.attempt
            and existing.get("pre_fingerprints", {}).get("sha256") == fingerprint
        ):
            output(
                {
                    "ok": True,
                    "already_exists": True,
                    "snapshot": str(before_path),
                    "stats": payload["stats"],
                }
            )
            return
        area = {
            "id": label,
            "label": args.label,
            "paths": [str(root) for root in roots],
            "coverage": existing.get("coverage", "names-only"),
            "execution": "in-progress",
            "cursor": "CONTOUR_READ",
            "cards": list(existing.get("cards", [])),
            "attempt": args.attempt,
            "pre_fingerprints": {
                "snapshot": str(before_path),
                "sha256": fingerprint,
                "roots": [str(root) for root in roots],
            },
            "post_fingerprints": {},
            "journal": existing.get("journal"),
            "saved_revision": existing.get("saved_revision"),
            "conflicts": list(existing.get("conflicts", [])),
            "note": existing.get("note", ""),
            "updated_at": utc_now(),
        }
        state["areas"][label] = area
        state["substate"] = "CONTOUR_READ"
        state["next_action"] = (
            f"Review only the approved content for area {args.label!r}."
        )
        save_state(
            brain_root,
            state,
            "contour-snapshot-created",
            {
                "area": label,
                "attempt": args.attempt,
                "fingerprint": fingerprint,
            },
        )
        output(
            {
                "ok": True,
                "already_exists": already_exists,
                "snapshot": str(before_path),
                "stats": payload["stats"],
            }
        )
        return

    before = read_json(before_path)
    expected_roots = [resolve_path(item["path"]) for item in before.get("roots", [])]
    if (
        before.get("brain_id") != state["brain_id"]
        or before.get("label") != label
        or before.get("attempt") != args.attempt
        or expected_roots != roots
    ):
        raise BrainCtlError("Snapshot identity or roots do not match this contour")
    after = snapshot_payload(
        roots,
        patterns,
        brain_id=state["brain_id"],
        label=label,
        attempt=args.attempt,
        excluded_roots=excluded_roots,
    )
    after_path = snapshot_dir / f"{label}-attempt-{args.attempt}-post.json"
    atomic_write_json(after_path, after)
    diff = snapshot_diff(before, after)
    fingerprint = hashlib.sha256(
        json.dumps(
            after,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    area = state.setdefault("areas", {}).get(label)
    if not isinstance(area, dict):
        raise BrainCtlError("Contour state is missing for this snapshot")
    area["post_fingerprints"] = {
        "snapshot": str(after_path),
        "sha256": fingerprint,
        "verified": diff["pass"],
        "roots": [str(root) for root in roots],
    }
    area["updated_at"] = utc_now()
    if diff["pass"]:
        area["cursor"] = "CONTOUR_CLOSE"
        state["substate"] = "CONTOUR_CLOSE"
        state["next_action"] = (
            f"Finish the cards and close area {args.label!r}."
        )
        event_name = "contour-snapshot-verified"
    else:
        area["coverage"] = "needs-recheck"
        area["execution"] = "in-progress"
        area["cursor"] = "CONTOUR_PRECHECK"
        state["substate"] = "CONTOUR_PRECHECK"
        state["next_action"] = (
            f"Source changed; restart area {args.label!r} from its pre-check."
        )
        event_name = "contour-source-changed"
    save_state(
        brain_root,
        state,
        event_name,
        {
            "area": label,
            "attempt": args.attempt,
            "fingerprint": fingerprint,
            "diff": diff,
        },
    )
    output({"ok": diff["pass"], "snapshot": str(after_path), "diff": diff})
    if not diff["pass"]:
        raise SystemExit(1)


def extract_frontmatter(path: Path) -> tuple[dict[str, str], set[str], str]:
    text = read_regular_utf8(
        path,
        max_size=2_000_000,
        description="memory card",
    )
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise BrainCtlError("missing opening frontmatter delimiter")
    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            end = index
            break
    if end is None:
        raise BrainCtlError("missing closing frontmatter delimiter")
    values: dict[str, str] = {}
    keys: set[str] = set()
    for line in lines[1:end]:
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        if key in keys:
            raise BrainCtlError(f"duplicate frontmatter key: {key}")
        keys.add(key)
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values, keys, "\n".join(lines[end + 1 :])


def card_paths(brain_root: Path) -> list[Path]:
    paths: list[Path] = []
    for root_name in KNOWLEDGE_ROOTS:
        root = brain_root / root_name
        if root.exists():
            paths.extend(path for path in root.rglob("*.md") if path.is_file())
    inbox = brain_root / "inbox"
    if inbox.exists():
        paths.extend(path for path in inbox.rglob("*.md") if path.is_file())
    return sorted(set(paths))


def validate_brain(
    brain_root: Path,
    *,
    validate_operating_state: bool = True,
    allow_core_profile_mismatch: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root_stat = brain_root.lstat()
    except OSError as exc:
        errors.append(f"cannot inspect private memory root: {exc}")
    else:
        if not statlib.S_ISDIR(root_stat.st_mode) or brain_root.is_symlink():
            errors.append("private memory root must be one real directory")
        if hasattr(os, "getuid") and root_stat.st_uid != os.getuid():
            errors.append("private memory root belongs to another local account")
        if root_stat.st_mode & 0o077:
            errors.append(
                "private memory root permissions allow another local account"
            )
    missing_dirs = [rel for rel in REQUIRED_DIRS if not (brain_root / rel).is_dir()]
    errors.extend(f"missing directory: {rel}" for rel in missing_dirs)
    for rel in REQUIRED_RUNTIME_FILES:
        path = brain_root / rel
        try:
            item = path.lstat()
        except OSError:
            errors.append(f"missing self-contained runtime file: {rel}")
            continue
        if (
            not statlib.S_ISREG(item.st_mode)
            or item.st_nlink != 1
            or path.is_symlink()
        ):
            errors.append(
                f"self-contained runtime file must be one regular file: {rel}"
            )

    for root_name in (*KNOWLEDGE_ROOTS, "inbox"):
        root = brain_root / root_name
        if root.is_symlink():
            errors.append(f"knowledge root must not be a symbolic link: {root_name}")
            continue
        if not root.exists() or not root.is_dir():
            continue
        for current, dir_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            for name in list(dir_names):
                child = current_path / name
                if child.is_symlink():
                    errors.append(
                        f"knowledge directory must not be a symbolic link: "
                        f"{child.relative_to(brain_root).as_posix()}"
                    )
                    dir_names.remove(name)
            for name in file_names:
                path = current_path / name
                relative = path.relative_to(brain_root).as_posix()
                try:
                    item = path.lstat()
                except OSError as exc:
                    errors.append(f"cannot inspect knowledge file {relative}: {exc}")
                    continue
                if (
                    not statlib.S_ISREG(item.st_mode)
                    or item.st_nlink != 1
                ):
                    errors.append(
                        f"knowledge file must be one regular file: {relative}"
                    )
                    continue
                if path.suffix.casefold() != ".md":
                    errors.append(
                        f"unsupported non-Markdown knowledge file: {relative}"
                    )

    card_report = validate_cards(brain_root)
    errors.extend(card_report["errors"])
    warnings.extend(card_report["warnings"])

    if validate_operating_state:
        try:
            state = read_json(state_path(brain_root))
            validate_state(state, brain_root)
        except (BrainCtlError, StateValidationError) as exc:
            errors.append(f"operating state: {exc}")
            state = None
        if state is not None:
            core_required = state.get("state") in CORE_REQUIRED_STATES or (
                state.get("state") == "PAUSED"
                and state.get("resume_from") in CORE_REQUIRED_STATES
            )
            review = state.get("core_review")
            if core_required and not isinstance(review, dict):
                errors.append(
                    "compact owner profile has not been explicitly confirmed"
                )
            if isinstance(review, dict):
                try:
                    current_profile_hash = core_profile_fingerprint(brain_root)
                except BrainCtlError as exc:
                    errors.append(f"compact owner profile: {exc}")
                else:
                    if (
                        current_profile_hash != review.get("profile_sha256")
                        and not allow_core_profile_mismatch
                    ):
                        errors.append(
                            "compact owner profile changed after owner confirmation"
                        )
        try:
            history = history_inspect(brain_root)
            if state is not None and history["head"] != state.get("expected_revision"):
                errors.append(
                    "local history head does not match the revision recorded in state"
                )
        except HistoryError as exc:
            errors.append(f"local history: {exc}")
        pending = pending_connection_transactions(brain_root)
        if pending:
            errors.append(
                f"{len(pending)} interrupted connection transaction(s) require recovery"
            )
        if state is not None:
            for connection_id, connection in state.get("connections", {}).items():
                if not isinstance(connection, dict):
                    continue
                if connection.get("status") not in {"connected", "verified"}:
                    continue
                target_value = connection.get("target")
                expected_hash = connection.get("after_sha256")
                if not isinstance(target_value, str) or not isinstance(
                    expected_hash, str
                ):
                    errors.append(
                        f"connection {connection_id!r} lacks target integrity data"
                    )
                    continue
                try:
                    target = resolve_path(target_value)
                    actual_bytes = read_connection_target(target)
                except (BrainCtlError, OSError) as exc:
                    errors.append(
                        f"connection {connection_id!r} cannot be verified: {exc}"
                    )
                    continue
                if hashlib.sha256(actual_bytes).hexdigest() != expected_hash:
                    errors.append(
                        f"connection {connection_id!r} no longer matches its verified bytes"
                    )
                try:
                    target_text = actual_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    errors.append(
                        f"connection {connection_id!r} is no longer UTF-8 text"
                    )
                else:
                    try:
                        metadata = connection_metadata(target_text)
                    except BrainCtlError as exc:
                        errors.append(
                            f"connection {connection_id!r} has malformed metadata: {exc}"
                        )
                        continue
                    if metadata is None or metadata[0] != state["brain_id"]:
                        errors.append(
                            f"connection {connection_id!r} lost its managed memory block"
                        )

    if (brain_root / ".git").exists() and git_available():
        remotes = run_git(brain_root, ["remote", "-v"], check=False).stdout.strip()
        if remotes:
            warnings.append(
                "A remote is configured; verify that the personal brain is not public."
            )
    return {
        "ok": not errors,
        "cards_checked": card_report["cards_checked"],
        "canonical_cards": card_report["canonical_cards"],
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }


def cmd_validate(args: argparse.Namespace) -> None:
    result = validate_brain(resolve_path(args.brain_root))
    output(result)
    if not result["ok"]:
        raise SystemExit(1)


def cmd_search(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    try:
        history = history_inspect(brain_root)
    except HistoryError as exc:
        raise BrainCtlError(
            f"Local history must be healthy before retrieval: {exc}"
        ) from exc
    if not history["clean"] or history["head"] != state.get("expected_revision"):
        raise BrainCtlError(
            "Refusing to retrieve from unsaved or revision-mismatched memory"
        )
    validation = validate_brain(brain_root)
    if not validation["ok"]:
        raise BrainCtlError(
            "Refusing to retrieve from invalid memory: "
            + "; ".join(validation["errors"])
        )
    result = search_cards(
        brain_root,
        args.query,
        limit=args.limit,
        include_history=args.include_history,
        as_of=args.as_of,
    )
    output(result)
    if not result["ok"]:
        raise SystemExit(1)


def render_connection_block(
    brain_root: Path,
    brain_id: str,
    *,
    newline: str = "\n",
    prefix_newline: bool = False,
) -> str:
    brain_root_text = str(brain_root)
    if (
        any(character in brain_root_text for character in ("\r", "\n", "\0", "`"))
        or START_MARKER in brain_root_text
        or END_MARKER in brain_root_text
    ):
        raise BrainCtlError(
            "The memory path cannot be represented safely in assistant instructions"
        )
    template = read_regular_utf8(
        POINTER_TEMPLATE,
        max_size=200_000,
        description="connection template",
    )
    rendered = (
        template.replace("{{BRAIN_ROOT}}", brain_root_text)
        .replace("{{BRAIN_ID}}", brain_id)
        .replace("{{PREFIX_NEWLINE}}", "1" if prefix_newline else "0")
        .rstrip()
        + "\n"
    )
    return rendered.replace("\n", newline)


def connection_metadata(original: str) -> tuple[str, bool] | None:
    start_count = original.count(START_MARKER)
    end_count = original.count(END_MARKER)
    if start_count == 0 and end_count == 0:
        return None
    if start_count != 1 or end_count != 1:
        raise BrainCtlError("Connection markers are duplicated or malformed")
    start = original.index(START_MARKER)
    end_start = original.index(END_MARKER)
    if end_start <= start:
        raise BrainCtlError("Connection markers are reversed or malformed")
    managed = original[start : end_start + len(END_MARKER)]
    metadata_pattern = re.compile(
        r"<!-- personal-brain-connection:v1 "
        r"brain-id=([0-9a-fA-F-]{8,128}) prefix-newline=([01]) -->"
    )
    metadata_matches = [
        metadata_pattern.fullmatch(line)
        for line in managed.splitlines()
        if "personal-brain-connection:" in line
    ]
    if len(metadata_matches) != 1 or metadata_matches[0] is None:
        raise BrainCtlError(
            "Connection block has missing or malformed ownership metadata"
        )
    match = metadata_matches[0]
    return match.group(1), match.group(2) == "1"


def connection_transform(
    original: str,
    block: str,
    *,
    remove: bool,
    prefix_newline: bool,
) -> str:
    start_count = original.count(START_MARKER)
    end_count = original.count(END_MARKER)
    if start_count != end_count or start_count > 1:
        raise BrainCtlError("Connection markers are duplicated or malformed")
    if start_count == 1:
        start = original.index(START_MARKER)
        try:
            end_start = original.index(END_MARKER, start)
        except ValueError as exc:
            raise BrainCtlError("Connection markers are reversed or malformed") from exc
        if start > 0 and original[start - 1] not in {"\n", "\r"}:
            raise BrainCtlError("Connection start marker is not line-bounded")
        start_after = start + len(START_MARKER)
        if start_after < len(original) and original[start_after] not in {"\n", "\r"}:
            raise BrainCtlError("Connection start marker has trailing content")
        if end_start > 0 and original[end_start - 1] not in {"\n", "\r"}:
            raise BrainCtlError("Connection end marker is not line-bounded")
        end_after = end_start + len(END_MARKER)
        if end_after < len(original) and original[end_after] not in {"\n", "\r"}:
            raise BrainCtlError("Connection end marker has trailing content")
    if remove:
        if start_count == 0:
            return original
        start = original.index(START_MARKER)
        end = original.index(END_MARKER, start) + len(END_MARKER)
        before = original[:start]
        after = original[end:]
        managed = original[start:end]
        if "prefix-newline=1" in managed:
            if before.endswith("\r\n"):
                before = before[:-2]
            elif before.endswith("\n"):
                before = before[:-1]
            else:
                raise BrainCtlError("Connection prefix-newline marker is inconsistent")
        if after.startswith("\r\n"):
            after = after[2:]
        elif after.startswith("\n"):
            after = after[1:]
        return before + after
    if start_count == 1:
        start = original.index(START_MARKER)
        end = original.index(END_MARKER, start) + len(END_MARKER)
        prefix = original[:start]
        suffix = original[end:]
        return prefix + block.rstrip("\r\n") + suffix
    if not original:
        return block
    separator = "\n" if prefix_newline else ""
    return original + separator + block


def validate_connection_target(target: Path) -> os.stat_result | None:
    if target.is_symlink():
        raise BrainCtlError(f"Refusing to edit a symlink: {target}")
    if target.exists():
        stat_result = target.lstat()
        if not statlib.S_ISREG(stat_result.st_mode):
            raise BrainCtlError(f"Connection target is not a regular file: {target}")
        if stat_result.st_nlink != 1:
            raise BrainCtlError(f"Refusing a hard-linked connection target: {target}")
        if hasattr(os, "getuid") and stat_result.st_uid != os.getuid():
            raise BrainCtlError(f"Connection target belongs to another user: {target}")
        if stat_result.st_size > 2_000_000:
            raise BrainCtlError(f"Connection target is unexpectedly large: {target}")
        if stat_result.st_mode & 0o022:
            raise BrainCtlError(
                f"Connection target is writable by another account or group: {target}"
            )
        return stat_result
    return None


def connection_target_fingerprint(target: Path) -> tuple[Any, ...]:
    result = validate_connection_target(target)
    if result is None:
        return ("missing",)
    return (
        "regular",
        result.st_dev,
        result.st_ino,
        result.st_nlink,
        result.st_size,
        getattr(
            result,
            "st_mtime_ns",
            int(result.st_mtime * 1_000_000_000),
        ),
        getattr(
            result,
            "st_ctime_ns",
            int(result.st_ctime * 1_000_000_000),
        ),
        statlib.S_IMODE(result.st_mode),
    )


def read_connection_target_with_fingerprint(
    target: Path,
) -> tuple[bytes, tuple[Any, ...]]:
    before = connection_target_fingerprint(target)
    data = read_connection_target(target)
    after = connection_target_fingerprint(target)
    if before != after:
        raise BrainCtlError(f"Connection target changed while being checked: {target}")
    return data, after


def unlink_connection_target_if_exact(
    target: Path,
    *,
    expected_bytes: bytes,
    expected_fingerprint: tuple[Any, ...],
) -> None:
    current_bytes, current_fingerprint = read_connection_target_with_fingerprint(
        target
    )
    if (
        current_fingerprint != expected_fingerprint
        or not hmac.compare_digest(current_bytes, expected_bytes)
    ):
        raise BrainCtlError(
            f"Connection target changed before safe removal: {target}"
        )
    target.unlink()


def read_connection_target(target: Path) -> bytes:
    expected = validate_connection_target(target)
    if expected is None:
        return b""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not statlib.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino)
            != (expected.st_dev, expected.st_ino)
        ):
            raise BrainCtlError(
                f"Connection target changed identity while opening: {target}"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 2_000_000:
                raise BrainCtlError(
                    f"Connection target became unexpectedly large: {target}"
                )
            chunks.append(chunk)
        after = os.fstat(descriptor)
        before_signature = (
            opened.st_dev,
            opened.st_ino,
            opened.st_nlink,
            opened.st_size,
            getattr(
                opened,
                "st_mtime_ns",
                int(opened.st_mtime * 1_000_000_000),
            ),
            getattr(
                opened,
                "st_ctime_ns",
                int(opened.st_ctime * 1_000_000_000),
            ),
        )
        after_signature = (
            after.st_dev,
            after.st_ino,
            after.st_nlink,
            after.st_size,
            getattr(
                after,
                "st_mtime_ns",
                int(after.st_mtime * 1_000_000_000),
            ),
            getattr(
                after,
                "st_ctime_ns",
                int(after.st_ctime * 1_000_000_000),
            ),
        )
        if before_signature != after_signature:
            raise BrainCtlError(f"Connection target changed while reading: {target}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def connection_preview(
    target: Path,
    brain_root: Path,
    brain_id: str,
    *,
    remove: bool,
) -> tuple[str, str, str, str]:
    original_bytes = read_connection_target(target)
    try:
        original = original_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrainCtlError(f"Connection target is not UTF-8 text: {target}") from exc
    if contains_secret_value_text(original):
        raise BrainCtlError(
            f"Refusing to preview or back up a secret-bearing connection target: {target}"
        )
    newline = "\r\n" if b"\r\n" in original_bytes else "\n"
    metadata = connection_metadata(original)
    has_block = metadata is not None
    if metadata is not None:
        recorded_brain_id, prefix_newline = metadata
        if recorded_brain_id != brain_id:
            raise BrainCtlError(
                "Connection markers belong to another or unknown personal brain"
            )
    else:
        prefix_newline = bool(original) and not original.endswith(("\n", "\r"))
    block = render_connection_block(
        brain_root,
        brain_id,
        newline=newline,
        prefix_newline=prefix_newline,
    )
    transformed = connection_transform(
        original,
        block,
        remove=remove,
        prefix_newline=prefix_newline,
    )
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            transformed.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target),
            n=0,
        )
    )
    token_payload = {
        "action": "remove" if remove else "connect",
        "brain_id": brain_id,
        "target": str(target),
        "before_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "after_sha256": hashlib.sha256(transformed.encode("utf-8")).hexdigest(),
    }
    token = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return transformed, diff, token, token_payload["before_sha256"]


def connection_transaction_dir(brain_root: Path) -> Path:
    return brain_root / "work" / "setup" / "transactions"


def pending_connection_transactions(brain_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    directory = connection_transaction_dir(brain_root)
    if not directory.exists():
        return []
    pending: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("connect-*.json")):
        payload = read_json(path)
        if payload.get("status") == "pending":
            pending.append((path, payload))
    return pending


def cmd_connect_recover(args: argparse.Namespace, brain_root: Path, state: dict[str, Any]) -> None:
    pending = pending_connection_transactions(brain_root)
    required_fields = {
        "schema_version",
        "status",
        "created_at",
        "brain_id",
        "action",
        "target",
        "before_exists",
        "before_sha256",
        "after_sha256",
        "backup",
        "connection_state_before",
    }
    for transaction_path, item in pending:
        if set(item) != required_fields:
            raise BrainCtlError(
                f"Connection transaction has an unexpected schema: {transaction_path}"
            )
        if (
            item.get("schema_version") != 1
            or item.get("status") != "pending"
            or item.get("brain_id") != state["brain_id"]
            or item.get("action") not in {"connect", "remove"}
            or not isinstance(item.get("before_exists"), bool)
            or not isinstance(item.get("connection_state_before"), (dict, type(None)))
        ):
            raise BrainCtlError(
                f"Connection transaction identity or fields are invalid: {transaction_path}"
            )
        for field in ("before_sha256", "after_sha256"):
            if not isinstance(item.get(field), str) or not re.fullmatch(
                r"[0-9a-f]{64}", item[field]
            ):
                raise BrainCtlError(
                    f"Connection transaction has an invalid {field}: {transaction_path}"
                )
        target = resolve_path(item.get("target", ""))
        if str(target) != item.get("target"):
            raise BrainCtlError(
                f"Connection transaction target is not canonical: {transaction_path}"
            )
        if not has_active_consent(state, "connection", [target]):
            raise BrainCtlError(
                f"Exact active connection consent is required to recover {target}"
            )
        recorded_connection = state.get("connections", {}).get(str(target))
        if not isinstance(recorded_connection, dict):
            raise BrainCtlError(
                f"Connection transaction target is not registered in state: {target}"
            )
        if recorded_connection.get("target") != str(target):
            raise BrainCtlError(
                f"Connection transaction target disagrees with state: {target}"
            )
        backup_path = resolve_path(item.get("backup", ""))
        backup_root = brain_root / "work" / "setup" / "backups"
        if not is_within(backup_path, backup_root) or backup_path.parent != backup_root:
            raise BrainCtlError(
                f"Connection transaction backup escapes its private directory: {backup_path}"
            )
        backup_bytes = read_connection_target(backup_path)
        if hashlib.sha256(backup_bytes).hexdigest() != item["before_sha256"]:
            raise BrainCtlError(
                f"Connection transaction backup failed integrity verification: {backup_path}"
            )
    summary = [
        {
            "transaction": path.name,
            "target": item.get("target"),
            "action": item.get("action"),
            "before_sha256": item.get("before_sha256"),
            "after_sha256": item.get("after_sha256"),
        }
        for path, item in pending
    ]
    token = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if args.action == "recover-preview":
        output(
            {
                "ok": True,
                "pending": summary,
                "confirmation_token": token if summary else None,
            }
        )
        return
    if not summary:
        output({"ok": True, "recovered": 0, "pending": []})
        return
    if args.token != token:
        raise BrainCtlError("Recovery token does not match pending transactions")

    recovered = 0
    for transaction_path, item in pending:
        target = resolve_path(item["target"])
        current_bytes, current_fingerprint = read_connection_target_with_fingerprint(
            target
        )
        current_hash = hashlib.sha256(current_bytes).hexdigest()
        before_hash = item["before_sha256"]
        after_hash = item["after_sha256"]
        if current_hash not in {before_hash, after_hash}:
            raise BrainCtlError(
                f"Connection target changed outside the transaction: {target}"
            )
        current_exists = current_fingerprint != ("missing",)
        if (
            current_hash == before_hash
            and current_exists != item["before_exists"]
        ):
            raise BrainCtlError(
                f"Connection target existence changed outside the transaction: {target}"
            )
        if current_hash == after_hash:
            if item.get("before_exists"):
                backup_path = resolve_path(item["backup"])
                backup_bytes = read_connection_target(backup_path)
                if hashlib.sha256(backup_bytes).hexdigest() != before_hash:
                    raise BrainCtlError(
                        f"Connection backup failed integrity verification: {backup_path}"
                    )
                atomic_replace_preserving_metadata(
                    target,
                    backup_bytes,
                    expected_before=current_bytes,
                    expected_fingerprint=current_fingerprint,
                )
            elif current_exists:
                unlink_connection_target_if_exact(
                    target,
                    expected_bytes=current_bytes,
                    expected_fingerprint=current_fingerprint,
                )
        previous = item.get("connection_state_before")
        connections = state.setdefault("connections", {})
        if previous is None:
            connections.pop(str(target), None)
        else:
            connections[str(target)] = previous
        item["status"] = "rolled-back"
        item["recovered_at"] = utc_now()
        atomic_write_json(transaction_path, item)
        recovered += 1
    save_state(
        brain_root,
        state,
        "connection-transactions-recovered",
        {"count": recovered},
    )
    output({"ok": True, "recovered": recovered})


def cmd_connect(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    if args.action in {"recover-preview", "recover"}:
        cmd_connect_recover(args, brain_root, state)
        return
    if not args.target:
        raise BrainCtlError("Connection target is required for this action")
    lexical_target = Path(args.target).expanduser().absolute()
    if lexical_target.is_symlink():
        raise BrainCtlError(f"Refusing to edit a symlink: {lexical_target}")
    target = lexical_target.resolve()
    remove = args.action.startswith("remove")
    recorded_connection = state.get("connections", {}).get(str(target))
    if not isinstance(recorded_connection, dict):
        raise BrainCtlError(
            "Connection target must first be recorded as an exact selected target"
        )
    if recorded_connection.get("target") != str(target):
        raise BrainCtlError("Connection target disagrees with its state record")
    if remove and recorded_connection.get("status") not in {
        "connected",
        "verified",
    }:
        raise BrainCtlError("Only a recorded connected target can be removed")
    if not remove and recorded_connection.get("status") not in {
        "pending-approval",
        "connected",
        "verified",
    }:
        raise BrainCtlError("Connection target is not ready for review or apply")
    consent_kind = (
        "connection-read"
        if args.action in {"preview", "remove-preview"}
        else "connection"
    )
    if not has_active_consent(state, consent_kind, [target]):
        raise BrainCtlError(
            f"Exact active {consent_kind} consent is required for {target}"
        )
    if remove:
        allowed_stages = {
            "CONNECTION_REVIEW",
            "CONNECTION_APPLY",
            "ACCEPTANCE_TESTING",
            "RECOVERY_COPY_REVIEW",
            "HANDOFF",
            "READY",
            "FAILED_CLOSED",
        }
        if state["state"] not in allowed_stages:
            raise BrainCtlError(
                "Connection removal is not available at the current guided stage"
            )
    elif args.action == "preview":
        if state["state"] not in {"CONNECTION_REVIEW", "CONNECTION_APPLY"}:
            raise BrainCtlError(
                "Connection preview is available only during connection review"
            )
    elif state["state"] != "CONNECTION_APPLY":
        raise BrainCtlError(
            "Connection apply is available only after the reviewed target is approved"
        )
    transformed, diff, token, before_sha256 = connection_preview(
        target,
        brain_root,
        state["brain_id"],
        remove=remove,
    )
    if args.action in {"preview", "remove-preview"}:
        unchanged = not diff
        output(
            {
                "ok": True,
                "target": str(target),
                "action": (
                    "already-removed"
                    if unchanged and remove
                    else "already-connected"
                    if unchanged
                    else "remove"
                    if remove
                    else "connect"
                ),
                "confirmation_token": token,
                "before_sha256": before_sha256,
                "diff": diff,
            }
        )
        return
    if args.token != token:
        raise BrainCtlError("Confirmation token does not match the current preview")
    if not target.parent.exists():
        raise BrainCtlError(f"Target parent directory does not exist: {target.parent}")
    original_bytes, original_fingerprint = read_connection_target_with_fingerprint(
        target
    )
    if hashlib.sha256(original_bytes).hexdigest() != before_sha256:
        raise BrainCtlError(
            "Connection target changed after preview; create a new preview"
        )
    original = original_bytes.decode("utf-8")
    if original == transformed:
        content_hash = hashlib.sha256(transformed.encode("utf-8")).hexdigest()
        output(
            {
                "ok": True,
                "target": str(target),
                "action": "already-removed" if remove else "already-connected",
                "changed": False,
                "content_sha256": content_hash,
            }
        )
        return
    timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    backup_dir = brain_root / "work" / "setup" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target_hash = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:12]
    backup_path = backup_dir / f"{target.name}.{target_hash}.{timestamp}.bak"
    atomic_replace_preserving_metadata(backup_path, original_bytes)
    connections = state.setdefault("connections", {})
    after_bytes = transformed.encode("utf-8")
    transaction_path = (
        connection_transaction_dir(brain_root) / f"connect-{uuid.uuid4().hex}.json"
    )
    original_exists = original_fingerprint != ("missing",)
    transaction = {
        "schema_version": 1,
        "status": "pending",
        "created_at": utc_now(),
        "brain_id": state["brain_id"],
        "action": "remove" if remove else "connect",
        "target": str(target),
        "before_exists": original_exists,
        "before_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "after_sha256": hashlib.sha256(after_bytes).hexdigest(),
        "backup": str(backup_path),
        "connection_state_before": connections.get(str(target)),
    }
    atomic_write_json(transaction_path, transaction)
    try:
        atomic_replace_preserving_metadata(
            target,
            after_bytes,
            expected_before=original_bytes,
            expected_fingerprint=original_fingerprint,
        )
        if read_connection_target(target) != after_bytes:
            raise BrainCtlError("Connection target verification failed")
        if remove:
            connections.pop(str(target), None)
            event_name = "brain-disconnected"
        else:
            reviewed = connections.get(str(target), {})
            connections[str(target)] = {
                "target": str(target),
                "selected": reviewed.get("selected", True),
                "connected_at": utc_now(),
                "backup": str(backup_path),
                "content_sha256": hashlib.sha256(after_bytes).hexdigest(),
                "confirmation_token": token,
                "before_sha256": before_sha256,
                "after_sha256": hashlib.sha256(after_bytes).hexdigest(),
                "status": "connected",
            }
            event_name = "brain-connected"
        state["next_action"] = (
            "Verify this exact assistant connection in a fresh conversation."
        )
        save_state(
            brain_root,
            state,
            event_name,
            {"target": str(target), "backup": str(backup_path)},
        )
    except Exception:
        rollback_complete = False
        try:
            current_bytes, current_fingerprint = (
                read_connection_target_with_fingerprint(target)
            )
            current_hash = hashlib.sha256(current_bytes).hexdigest()
            if current_hash == transaction["before_sha256"]:
                rollback_complete = True
            elif current_hash == transaction["after_sha256"]:
                if transaction["before_exists"]:
                    atomic_replace_preserving_metadata(
                        target,
                        original_bytes,
                        expected_before=current_bytes,
                        expected_fingerprint=current_fingerprint,
                    )
                else:
                    unlink_connection_target_if_exact(
                        target,
                        expected_bytes=current_bytes,
                        expected_fingerprint=current_fingerprint,
                    )
                rollback_complete = True
        except Exception:
            rollback_complete = False
        if rollback_complete:
            transaction["status"] = "rolled-back"
            transaction["recovered_at"] = utc_now()
            atomic_write_json(transaction_path, transaction)
        raise
    transaction["status"] = "complete"
    transaction["completed_at"] = utc_now()
    atomic_write_json(transaction_path, transaction)
    output(
        {
            "ok": True,
            "target": str(target),
            "action": "removed" if remove else "connected",
            "changed": True,
            "backup": str(backup_path),
            "content_sha256": hashlib.sha256(after_bytes).hexdigest(),
        }
    )


def cmd_save(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    ensure_record_text_is_safe(
        {
            "summary": args.summary,
            "reason": args.reason,
            "core_note": args.core_note or "",
        }
    )
    requested = {Path(raw).as_posix() for raw in args.paths}
    for rel in requested:
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise BrainCtlError(f"Path must be brain-relative: {rel}")
        parts = Path(rel).parts
        if not parts or (
            parts[0] not in {*KNOWLEDGE_ROOTS, "inbox"}
            and rel != "START-HERE.md"
        ):
            raise BrainCtlError(
                f"Cannot save memory knowledge outside canonical areas: {rel}"
            )
        candidate = brain_root / rel
        if candidate.exists() or candidate.is_symlink():
            item = candidate.lstat()
            if (
                not statlib.S_ISREG(item.st_mode)
                or item.st_nlink != 1
                or candidate.is_symlink()
            ):
                raise BrainCtlError(
                    f"Memory path must be one regular file before save: {rel}"
                )
            if item.st_size > 2_000_000:
                raise BrainCtlError(f"Memory file is unexpectedly large: {rel}")
            try:
                candidate_text = read_regular_utf8(
                    candidate,
                    max_size=2_000_000,
                    description="memory file",
                )
            except (OSError, UnicodeDecodeError) as exc:
                raise BrainCtlError(
                    f"Memory file must be safe UTF-8 text: {rel} ({exc})"
                ) from exc
            if contains_secret_text(candidate_text):
                raise BrainCtlError(
                    f"Refusing to save a secret-like value in memory: {rel}"
                )
    try:
        history_before = history_inspect(brain_root)
    except HistoryError as exc:
        raise BrainCtlError(f"Local history could not be inspected: {exc}") from exc
    if history_before["head"] != state.get("expected_revision"):
        raise BrainCtlError(
            "Local history and durable state disagree; use the guided history "
            "reconciliation before saving another change"
        )
    actual = set(
        history_before["added"]
        + history_before["modified"]
        + history_before["deleted"]
    )
    unexpected = sorted(actual - requested)
    missing = sorted(requested - actual)
    if unexpected or missing:
        raise BrainCtlError(
            "Changed paths do not match the exact requested set",
        )
    profile_changed = "profile/owner.md" in requested
    if (
        profile_changed
        and isinstance(state.get("core_review"), dict)
        and not args.confirm_core_profile
    ):
        raise BrainCtlError(
            "A previously confirmed compact profile requires "
            "--confirm-core-profile and a new --core-note"
        )
    if args.confirm_core_profile and (
        not profile_changed or not args.core_note or not args.core_note.strip()
    ):
        raise BrainCtlError(
            "--confirm-core-profile requires profile/owner.md and a non-empty --core-note"
        )
    validation = validate_brain(
        brain_root,
        allow_core_profile_mismatch=bool(
            profile_changed and args.confirm_core_profile
        ),
    )
    if not validation["ok"]:
        details = "; ".join(validation["errors"])
        raise BrainCtlError(f"Brain validation failed: {details}")
    try:
        revision = history_save(
            brain_root,
            sorted(requested),
            args.summary,
            args.reason,
        )
    except HistoryError as exc:
        raise BrainCtlError(f"Local history save failed: {exc}") from exc
    state["expected_revision"] = revision
    if args.confirm_core_profile:
        state["core_review"] = {
            "confirmed_at": utc_now(),
            "profile_sha256": core_profile_fingerprint(brain_root),
            "note": args.core_note,
        }
    save_state(
        brain_root,
        state,
        "memory-revision-saved",
        {
            "revision": revision,
            "paths": sorted(requested),
            "summary": args.summary,
            "reason": args.reason,
        },
    )
    output(
        {
            "ok": True,
            "revision": revision,
            "paths": sorted(requested),
            "summary": args.summary,
            "reason": args.reason,
        }
    )


def _effective_resume_stage(state: dict[str, Any]) -> str:
    current = state.get("state")
    if current == "PAUSED":
        return state.get("resume_from") or "DISCOVER_OR_RESUME"
    if current == "FAILED_CLOSED":
        return state.get("failed_from") or "DISCOVER_OR_RESUME"
    return current if current in STATES else "DISCOVER_OR_RESUME"


def _earlier_stage(first: str, second: str) -> str:
    return min(
        (first, second),
        key=lambda value: TOP_LEVEL_STATES.index(value),
    )


def invalidate_after_history_change(
    state: dict[str, Any],
    brain_root: Path,
    *,
    kind: str,
    issue: str,
) -> None:
    """Move durable workflow state to a safe re-check point after restore/reconcile."""

    resume_stage = _effective_resume_stage(state)
    if TOP_LEVEL_STATES.index(resume_stage) >= TOP_LEVEL_STATES.index(
        "LIBRARY_VALIDATION"
    ):
        resume_stage = "LIBRARY_VALIDATION"

    state["checks"] = {}
    connections = state.get("connections", {})
    if isinstance(connections, dict):
        for connection in connections.values():
            if not isinstance(connection, dict):
                continue
            if connection.get("status") == "verified":
                connection["status"] = "connected"
                connection.pop("evidence", None)
                connection.pop("test_result", None)

    core_review = state.get("core_review")
    if isinstance(core_review, dict):
        try:
            current_profile = core_profile_fingerprint(brain_root)
        except BrainCtlError:
            current_profile = None
        if current_profile != core_review.get("profile_sha256"):
            state.pop("core_review", None)
            resume_stage = _earlier_stage(resume_stage, "CORE_REVIEW")

    if kind == "undo":
        areas = state.get("areas", {})
        if isinstance(areas, dict):
            for area in areas.values():
                if (
                    isinstance(area, dict)
                    and area.get("coverage") == "content-reviewed"
                ):
                    area["coverage"] = "needs-recheck"
                    area["execution"] = "pending"
                    resume_stage = _earlier_stage(resume_stage, "CONTOUR_LOOP")
        session_history = state.get("session_history", {})
        if isinstance(session_history, dict):
            for source in session_history.values():
                if (
                    isinstance(source, dict)
                    and source.get("coverage")
                    in {"distillates-reviewed", "transcripts-reviewed"}
                ):
                    source["coverage"] = "needs-recheck"
                    source["execution"] = "pending"
                    resume_stage = _earlier_stage(
                        resume_stage,
                        "SESSION_HISTORY_AUDIT",
                    )

    state["state"] = "FAILED_CLOSED"
    state["substate"] = None
    state["failed_from"] = resume_stage
    state["blocking_issue"] = issue
    state["next_action"] = None
    state["authorized_write_set"] = []
    state.pop("resume_from", None)
    state.pop("recovery_pending", None)


def history_reconciliation_preview(
    brain_root: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    try:
        inspected = history_inspect(brain_root)
        revisions = history_list_revisions(brain_root)
    except HistoryError as exc:
        raise BrainCtlError(
            f"Local history is not healthy enough to reconcile: {exc}"
        ) from exc
    if not inspected["clean"]:
        raise BrainCtlError(
            "History reconciliation requires a clean memory working tree"
        )
    expected = state.get("expected_revision")
    head = inspected["head"]
    if head == expected:
        return {
            "needed": False,
            "from_revision": expected,
            "to_revision": head,
            "confirmation_token": None,
        }
    by_id = {item.get("id"): item for item in revisions}
    head_revision = by_id.get(head)
    if (
        not isinstance(expected, str)
        or expected not in by_id
        or not isinstance(head_revision, dict)
        or head_revision.get("parent") != expected
        or head_revision.get("kind") not in {"save", "undo"}
    ):
        raise BrainCtlError(
            "History mismatch is not one exact unpublished state update; "
            "automatic reconciliation would be ambiguous"
        )
    token_payload = {
        "action": "reconcile-history-state",
        "brain_id": state["brain_id"],
        "event_seq": state["event_seq"],
        "from_revision": expected,
        "to_revision": head,
        "kind": head_revision["kind"],
        "changed_paths": head_revision["changed_paths"],
    }
    token = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "needed": True,
        "from_revision": expected,
        "to_revision": head,
        "kind": head_revision["kind"],
        "changed_paths": head_revision["changed_paths"],
        "confirmation_token": token,
    }


def cmd_history(args: argparse.Namespace) -> None:
    brain_root = resolve_path(args.brain_root)
    state = load_state(brain_root)
    if args.reason:
        ensure_record_text_is_safe({"history_reason": args.reason})
    try:
        if args.action == "status":
            result = history_inspect(brain_root)
            matches = result["head"] == state.get("expected_revision")
            payload = {
                "ok": result["integrity"] == "ok" and result["clean"] and matches,
                **result,
                "revision_matches_state": matches,
            }
            output(payload)
            if not payload["ok"]:
                raise SystemExit(1)
            return
        if args.action == "list":
            output(
                {
                    "ok": True,
                    "revisions": history_list_revisions(brain_root),
                }
            )
            return
        if args.action == "recover-preview":
            output(
                {
                    "ok": True,
                    **history_recovery_preview(brain_root),
                }
            )
            return
        if args.action == "recover":
            if not args.token:
                raise BrainCtlError("--token is required for history recovery")
            recovered = history_recovery_apply(brain_root, args.token)
            if recovered["action"] == "cleanup-published-undo":
                state["expected_revision"] = recovered["head"]
                invalidate_after_history_change(
                    state,
                    brain_root,
                    kind="undo",
                    issue=(
                        "An interrupted memory restore was completed. "
                        "Review the restored knowledge before continuing."
                    ),
                )
            save_state(
                brain_root,
                state,
                "portable-history-recovered",
                recovered,
            )
            validation = validate_brain(brain_root)
            if not validation["ok"]:
                raise BrainCtlError(
                    "Recovered history failed validation: "
                    + "; ".join(validation["errors"])
                )
            output({"ok": True, **recovered})
            return
        if args.action == "reconcile-preview":
            output(
                {
                    "ok": True,
                    **history_reconciliation_preview(brain_root, state),
                }
            )
            return
        if args.action == "reconcile":
            if not args.token:
                raise BrainCtlError(
                    "--token is required for history state reconciliation"
                )
            reconciliation = history_reconciliation_preview(brain_root, state)
            expected_token = reconciliation.get("confirmation_token")
            if not reconciliation["needed"] or not isinstance(
                expected_token,
                str,
            ):
                raise BrainCtlError(
                    "History state does not require reconciliation"
                )
            if not hmac.compare_digest(args.token, expected_token):
                raise BrainCtlError(
                    "History reconciliation token is stale or invalid"
                )
            state["expected_revision"] = reconciliation["to_revision"]
            kind = reconciliation["kind"]
            invalidate_after_history_change(
                state,
                brain_root,
                kind=kind,
                issue=(
                    "A completed memory history change was recovered after "
                    "an interrupted state update. Review the affected "
                    "knowledge before continuing."
                ),
            )
            save_state(
                brain_root,
                state,
                "portable-history-state-reconciled",
                {
                    "from_revision": reconciliation["from_revision"],
                    "to_revision": reconciliation["to_revision"],
                    "kind": kind,
                    "changed_paths": reconciliation["changed_paths"],
                },
            )
            validation = validate_brain(brain_root)
            if not validation["ok"]:
                raise BrainCtlError(
                    "Reconciled history failed validation: "
                    + "; ".join(validation["errors"])
                )
            output(
                {
                    "ok": True,
                    "reconciled": True,
                    "from_revision": reconciliation["from_revision"],
                    "to_revision": reconciliation["to_revision"],
                    "kind": kind,
                    "changed_paths": reconciliation["changed_paths"],
                }
            )
            return
        if not args.to_revision:
            raise BrainCtlError("--to-revision is required for undo")
        if args.action == "undo-preview":
            output(
                {
                    "ok": True,
                    **history_undo_preview(brain_root, args.to_revision),
                }
            )
            return
        if not args.token or not args.reason:
            raise BrainCtlError("--token and --reason are required for undo")
        revision = history_undo_apply(
            brain_root,
            args.to_revision,
            args.token,
            args.reason,
        )
    except HistoryError as exc:
        raise BrainCtlError(f"Local history operation failed: {exc}") from exc

    state["expected_revision"] = revision
    invalidate_after_history_change(
        state,
        brain_root,
        kind="undo",
        issue=(
            "A previous memory revision was restored. Review the restored "
            "knowledge before continuing."
        ),
    )
    save_state(
        brain_root,
        state,
        "memory-revision-restored",
        {
            "revision": revision,
            "restored_from": args.to_revision,
            "reason": args.reason,
        },
    )
    validation = validate_brain(brain_root)
    if not validation["ok"]:
        raise BrainCtlError(
            "Restored revision failed validation: "
            + "; ".join(validation["errors"])
        )
    output(
        {
            "ok": True,
            "revision": revision,
            "restored_from": args.to_revision,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control and validate a private cross-project personal brain."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check whether setup can run safely")
    doctor.add_argument("--path", help="Candidate brain directory")
    doctor.add_argument(
        "--pointer-file",
        help="Exact private pointer file that would be created in the builder",
    )
    doctor.set_defaults(func=cmd_doctor)

    init = sub.add_parser("init", help="Create a new private brain")
    init.add_argument("--brain-root", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--locale", default="ru")
    init.add_argument("--pointer-file")
    init.add_argument("--preview-token", required=True)
    init.add_argument("--approval-note", required=True)
    init.set_defaults(func=cmd_init)

    init_claim = sub.add_parser(
        "init-claim",
        help="Inspect or clear a stale interrupted memory-creation reservation",
    )
    init_claim.add_argument(
        "action",
        choices=("inspect", "clear-preview", "clear"),
    )
    init_claim.add_argument("--pointer-file", required=True)
    init_claim.add_argument("--token")
    init_claim.set_defaults(func=cmd_init_claim)

    status = sub.add_parser("status", help="Show setup and integrity status")
    status.add_argument("--brain-root", required=True)
    status.set_defaults(func=cmd_status)

    lock = sub.add_parser("lock", help="Inspect or clear an interrupted operation lock")
    lock.add_argument("action", choices=("inspect", "clear-preview", "clear"))
    lock.add_argument("--brain-root", required=True)
    lock.add_argument("--token")
    lock.set_defaults(func=cmd_lock)

    state = sub.add_parser("state", help="Show or advance the setup state")
    state.add_argument("--brain-root", required=True)
    state_group = state.add_mutually_exclusive_group(required=True)
    state_group.add_argument("--show", action="store_true")
    state_group.add_argument("--stage")
    state.add_argument("--next-action")
    state.add_argument("--blocking-issue")
    state.add_argument("--recovery", action="store_true")
    state.set_defaults(func=cmd_state)

    record = sub.add_parser(
        "record",
        help="Record an approval, area result, or acceptance check",
    )
    record_sub = record.add_subparsers(dest="record_kind", required=True)

    onboarding = record_sub.add_parser(
        "onboarding",
        help="Persist the confirmed profile seed and safety choices",
    )
    onboarding.add_argument("--brain-root", required=True)
    onboarding_summary = onboarding.add_mutually_exclusive_group(required=True)
    onboarding_summary.add_argument("--work-summary")
    onboarding_summary.add_argument(
        "--withhold-work-summary",
        action="store_true",
    )
    onboarding.add_argument("--desired-outcome", required=True)
    onboarding.add_argument(
        "--computer-use",
        choices=("private", "shared", "unknown", "withheld"),
        required=True,
    )
    onboarding.add_argument(
        "--processing-mode",
        choices=("local", "online-service", "unknown"),
        required=True,
    )
    onboarding.add_argument("--processing-note", required=True)
    onboarding.add_argument(
        "--capability",
        choices=("automatic", "manual-connection"),
        required=True,
    )
    onboarding.add_argument("--never-open", nargs="*", default=[])
    onboarding.add_argument("--read-not-store", nargs="*", default=[])
    onboarding.add_argument("--manual-steps", nargs="*", default=[])
    onboarding.add_argument("--introduction-confirmed", action="store_true")
    onboarding.add_argument("--processing-acknowledged", action="store_true")

    consent = record_sub.add_parser("consent", help="Append an owner decision")
    consent.add_argument("--brain-root", required=True)
    consent.add_argument("--kind", choices=sorted(CONSENT_KINDS), required=True)
    consent.add_argument(
        "--decision",
        choices=sorted(CONSENT_DECISIONS),
        required=True,
    )
    consent.add_argument("--scope", nargs="+", required=True)
    consent.add_argument("--note")

    area = record_sub.add_parser("area", help="Record one audited area's status")
    area.add_argument("--brain-root", required=True)
    area.add_argument("--id", required=True)
    area.add_argument("--label", required=True)
    area.add_argument("--paths", nargs="+", required=True)
    area_coverage = area.add_mutually_exclusive_group(required=True)
    area_coverage.add_argument("--coverage", choices=sorted(AREA_COVERAGE))
    area_coverage.add_argument(
        "--status",
        choices=sorted(AREA_COVERAGE),
        help=argparse.SUPPRESS,
    )
    area.add_argument("--execution", choices=sorted(AREA_EXECUTION))
    area.add_argument("--cursor")
    area.add_argument("--cards", nargs="*")
    area.add_argument("--attempt", type=int, default=0)
    area.add_argument("--journal")
    area.add_argument("--saved-revision")
    area.add_argument("--conflicts", nargs="*")
    area.add_argument("--note")

    check_parser = record_sub.add_parser(
        "check",
        help="Record one acceptance check",
    )
    check_parser.add_argument("--brain-root", required=True)
    check_parser.add_argument("--id", required=True)
    check_parser.add_argument(
        "--status",
        choices=sorted(CHECK_STATUSES),
        required=True,
    )
    check_parser.add_argument("--evidence", required=True)

    core = record_sub.add_parser(
        "core",
        help="Confirm the compact owner profile after owner review",
    )
    core.add_argument("--brain-root", required=True)
    core.add_argument("--note", required=True)

    session = record_sub.add_parser(
        "session",
        help="Record one approved assistant-history source",
    )
    session.add_argument("--brain-root", required=True)
    session.add_argument("--id", required=True)
    session.add_argument("--program", required=True)
    session.add_argument("--path")
    session.add_argument("--date-from")
    session.add_argument("--date-to")
    session.add_argument(
        "--execution",
        choices=("pending", "in-progress", "closed"),
        required=True,
    )
    session.add_argument("--coverage", choices=sorted(SESSION_COVERAGE), required=True)
    session.add_argument("--cursor")
    session.add_argument("--exclusions", nargs="*")
    session.add_argument("--failures", nargs="*")
    session.add_argument("--note")

    connection_record = record_sub.add_parser(
        "connection",
        help="Record connection verification state",
    )
    connection_record.add_argument("--brain-root", required=True)
    connection_record.add_argument("--target", required=True)
    connection_record.add_argument(
        "--status",
        choices=sorted(CONNECTION_STATUSES),
        required=True,
    )
    connection_record.add_argument(
        "--selected",
        choices=("yes", "no"),
        default="yes",
    )
    connection_record.add_argument("--evidence")
    record.set_defaults(func=cmd_record)

    scope = sub.add_parser("scope", help="Record an exact user-approved source scope")
    scope.add_argument("--brain-root", required=True)
    scope.add_argument("--path", required=True)
    scope.add_argument("--mode", choices=sorted(SCOPE_MODES), required=True)
    scope.add_argument("--label")
    scope.add_argument("--reason")
    scope.set_defaults(func=cmd_scope)

    inventory = sub.add_parser("inventory", help="Build a names-only metadata inventory")
    inventory.add_argument("--brain-root", required=True)
    inventory.add_argument("--roots", nargs="+", required=True)
    inventory.set_defaults(func=cmd_inventory)

    snapshot = sub.add_parser("snapshot", help="Create or verify a read-only source snapshot")
    snapshot.add_argument("action", choices=("create", "verify"))
    snapshot.add_argument("--brain-root", required=True)
    snapshot.add_argument("--root", nargs="+", required=True)
    snapshot.add_argument("--label", required=True)
    snapshot.add_argument("--attempt", type=int, default=1)
    snapshot.set_defaults(func=cmd_snapshot)

    validate = sub.add_parser("validate", help="Validate brain structure and cards")
    validate.add_argument("--brain-root", required=True)
    validate.set_defaults(func=cmd_validate)

    search = sub.add_parser("search", help="Search active knowledge cards")
    search.add_argument("--brain-root", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=6)
    search.add_argument("--include-history", action="store_true")
    search.add_argument("--as-of")
    search.set_defaults(func=cmd_search)

    connect = sub.add_parser("connect", help="Preview, add, or remove a bounded connection block")
    connect.add_argument(
        "action",
        choices=(
            "preview",
            "apply",
            "remove-preview",
            "remove",
            "recover-preview",
            "recover",
        ),
    )
    connect.add_argument("--brain-root", required=True)
    connect.add_argument("--target")
    connect.add_argument("--token")
    connect.set_defaults(func=cmd_connect)

    save = sub.add_parser("save", help="Validate and save one exact semantic memory change")
    save.add_argument("--brain-root", required=True)
    save.add_argument("--paths", nargs="+", required=True)
    save.add_argument("--summary", required=True)
    save.add_argument("--reason", required=True)
    save.add_argument("--confirm-core-profile", action="store_true")
    save.add_argument("--core-note")
    save.set_defaults(func=cmd_save)

    history = sub.add_parser(
        "history",
        help="Inspect or safely restore the built-in local history",
    )
    history.add_argument(
        "action",
        choices=(
            "status",
            "list",
            "undo-preview",
            "undo",
            "recover-preview",
            "recover",
            "reconcile-preview",
            "reconcile",
        ),
    )
    history.add_argument("--brain-root", required=True)
    history.add_argument("--to-revision")
    history.add_argument("--token")
    history.add_argument("--reason")
    history.set_defaults(func=cmd_history)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        mutating = (
            args.command in {"record", "scope", "inventory", "snapshot", "save"}
            or (args.command == "state" and not args.show)
            or (
                args.command == "connect"
                and args.action in {"apply", "remove", "recover"}
            )
            or (
                args.command == "history"
                and args.action in {"undo", "recover", "reconcile"}
            )
        )
        if mutating:
            brain_root = resolve_path(args.brain_root)
            with setup_operation_lock(brain_root, f"{args.command}:{getattr(args, 'action', '')}"):
                args.func(args)
        else:
            args.func(args)
    except BrainCtlError as exc:
        fail(str(exc))
    except PermissionError as exc:
        fail("Permission denied", details=str(exc))


if __name__ == "__main__":
    main()
