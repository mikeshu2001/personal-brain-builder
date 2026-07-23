#!/usr/bin/env python3
"""Dependency-free, content-addressed local history for a personal brain.

The working tree remains ordinary files. Immutable blobs, full manifests, and
forward-only revisions live under ``.brain-history``. A revision becomes
visible only when ``HEAD`` is atomically replaced.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import hashlib
import hmac
import json
import os
import platform
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Set, Tuple


HISTORY_NAME = ".brain-history"
FORMAT_NAME = "portable-brain-history"
FORMAT_VERSION = 1
HASH_NAME = "sha256"
ID_LENGTH = 64
CHUNK_SIZE = 1024 * 1024
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700


class HistoryError(RuntimeError):
    """Raised when an operation cannot preserve history invariants."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _canonical_bytes(payload: Any) -> bytes:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HistoryError("History payload is not canonical JSON") from exc
    return text.encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_id(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != ID_LENGTH:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoryError("%s must be non-empty" % label)
    return value.strip()


def _valid_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == dt.timedelta(0)
    )


def _pid_is_alive(pid: Any) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _private_regular_metadata(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise HistoryError("Missing private history file: %s" % path) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise HistoryError("Private history file is unsafe: %s" % path)
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise HistoryError("Private history file permissions are unsafe: %s" % path)
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise HistoryError("Private history file belongs to another user: %s" % path)
    return metadata


def _private_directory_metadata(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise HistoryError("Missing private history directory: %s" % path) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise HistoryError("Private history directory is unsafe: %s" % path)
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise HistoryError(
            "Private history directory permissions are unsafe: %s" % path
        )
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise HistoryError(
            "Private history directory belongs to another user: %s" % path
        )
    return metadata


def _ensure_private_directories(base: Path, directory: Path) -> None:
    try:
        relative = directory.relative_to(base)
    except ValueError as exc:
        raise HistoryError("Private directory path escapes its base") from exc
    current = base
    _private_directory_metadata(base)
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _private_directory_metadata(current)
        else:
            current.mkdir(mode=PRIVATE_DIRECTORY_MODE)


def _normalize_root(raw_root: Any) -> Path:
    candidate = Path(raw_root).expanduser().absolute()
    if candidate.is_symlink():
        raise HistoryError("Brain root must not be a symlink: %s" % candidate)
    if not candidate.exists() or not candidate.is_dir():
        raise HistoryError("Brain root must be an existing directory: %s" % candidate)
    return candidate.resolve()


def _history_path(root: Path) -> Path:
    return root / HISTORY_NAME


def _is_excluded_parts(parts: Tuple[str, ...]) -> bool:
    if not parts:
        return False
    folded = tuple(part.casefold() for part in parts)
    if any(
        part in {".git", ".brain-history"} or part.startswith(".brain-history-init-")
        for part in folded
    ):
        return True
    if any(
        folded[index : index + 2] == ("work", "setup")
        for index in range(max(0, len(folded) - 1))
    ):
        return True
    if folded[0] in {"cache", ".cache", "caches", "backups"}:
        return True
    if "__pycache__" in folded:
        return True
    return False


def _validate_relative(raw_path: Any, *, allow_excluded: bool = False) -> str:
    try:
        value = os.fspath(raw_path)
    except TypeError as exc:
        raise HistoryError("History path must be text or path-like") from exc
    if not isinstance(value, str) or not value:
        raise HistoryError("History path must be non-empty")
    normalized_separators = value.replace("\\", "/")
    if normalized_separators.startswith("/"):
        raise HistoryError("History path must be relative: %s" % value)
    parts = normalized_separators.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts) or ":" in parts[0]:
        raise HistoryError("Unsafe history path: %s" % value)
    normalized = "/".join(parts)
    if not allow_excluded and _is_excluded_parts(tuple(parts)):
        raise HistoryError("Path is excluded from portable history: %s" % normalized)
    return normalized


def _path_for(root: Path, relative: str) -> Path:
    normalized = _validate_relative(relative)
    candidate = root.joinpath(*normalized.split("/"))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HistoryError(
            "History path escapes the brain root: %s" % relative
        ) from exc
    return candidate


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name,
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(str(temporary), mode)
        except OSError:
            pass
        os.replace(str(temporary), str(path))
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, payload: Any, mode: int = 0o600) -> None:
    _atomic_write(path, _canonical_bytes(payload) + b"\n", mode=mode)


def _safe_read_regular(path: Path) -> Tuple[bytes, int]:
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise HistoryError("Missing required file: %s" % path) from exc
    if stat.S_ISLNK(before.st_mode):
        raise HistoryError("Symlinks are not allowed in tracked history: %s" % path)
    if not stat.S_ISREG(before.st_mode):
        raise HistoryError("Only regular files can be tracked: %s" % path)
    if before.st_nlink != 1:
        raise HistoryError("Hard-linked files are not allowed: %s" % path)

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise HistoryError("Cannot safely open tracked file: %s" % path) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise HistoryError("Tracked file changed identity while opening: %s" % path)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise HistoryError("Tracked file changed identity while opening: %s" % path)
        chunks: List[bytes] = []
        while True:
            chunk = os.read(descriptor, CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
            raise HistoryError(
                "Tracked file changed link state while being read: %s" % path
            )
        before_signature = (
            opened.st_dev,
            opened.st_ino,
            opened.st_nlink,
            opened.st_size,
            getattr(opened, "st_mtime_ns", int(opened.st_mtime * 1_000_000_000)),
            stat.S_IMODE(opened.st_mode),
        )
        after_signature = (
            after.st_dev,
            after.st_ino,
            after.st_nlink,
            after.st_size,
            getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
            stat.S_IMODE(after.st_mode),
        )
        if before_signature != after_signature:
            raise HistoryError("Tracked file changed while being read: %s" % path)
        return b"".join(chunks), stat.S_IMODE(after.st_mode)
    finally:
        os.close(descriptor)


def _scan_tree(root: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, bytes]]:
    entries: Dict[str, Dict[str, Any]] = {}
    blob_data: Dict[str, bytes] = {}

    def walk(directory: Path, relative_parts: Tuple[str, ...]) -> None:
        try:
            children = sorted(os.scandir(str(directory)), key=lambda item: item.name)
        except OSError as exc:
            raise HistoryError(
                "Cannot enumerate brain directory: %s" % directory
            ) from exc
        for child in children:
            child_parts = relative_parts + (child.name,)
            if _is_excluded_parts(child_parts):
                continue
            path = directory / child.name
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise HistoryError("Cannot inspect brain path: %s" % path) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise HistoryError(
                    "Symlinks are not allowed in tracked history: %s" % path
                )
            if stat.S_ISDIR(metadata.st_mode):
                walk(path, child_parts)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise HistoryError("Only regular files can be tracked: %s" % path)
            data, mode = _safe_read_regular(path)
            relative = _validate_relative("/".join(child_parts))
            blob_id = _digest(data)
            entries[relative] = {
                "blob": blob_id,
                "size": len(data),
                "mode": mode,
            }
            blob_data.setdefault(blob_id, data)

    walk(root, ())
    return dict(sorted(entries.items())), blob_data


def _assert_history_directory(history: Path) -> None:
    try:
        metadata = history.lstat()
    except FileNotFoundError as exc:
        raise HistoryError("Portable history is not initialized: %s" % history) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise HistoryError("Portable history root is unsafe: %s" % history)


def _assert_safe_internal_parent(history: Path, path: Path) -> None:
    try:
        relative = path.relative_to(history)
    except ValueError as exc:
        raise HistoryError("History object escapes internal storage") from exc
    current = history
    _assert_history_directory(history)
    for part in relative.parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise HistoryError(
                "Missing history object directory: %s" % current
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise HistoryError("Unsafe history object directory: %s" % current)


def _read_internal(history: Path, path: Path) -> bytes:
    _assert_safe_internal_parent(history, path)
    return _safe_read_regular(path)[0]


def _ensure_internal_write_parent(history: Path, path: Path) -> None:
    try:
        relative = path.parent.relative_to(history)
    except ValueError as exc:
        raise HistoryError("History object escapes internal storage") from exc
    _assert_history_directory(history)
    current = history
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise HistoryError("Unsafe history object directory: %s" % current)
        else:
            current.mkdir(mode=0o700)


def _load_json(history: Path, path: Path) -> Any:
    data = _read_internal(history, path)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryError("Corrupt JSON history object: %s" % path) from exc
    if data != _canonical_bytes(payload) + b"\n":
        raise HistoryError("Non-canonical JSON history object: %s" % path)
    return payload


def _blob_path(history: Path, blob_id: str) -> Path:
    if not _valid_id(blob_id):
        raise HistoryError("Invalid blob identifier")
    return history / "blobs" / HASH_NAME / blob_id[:2] / blob_id[2:]


def _object_path(history: Path, kind: str, object_id: str) -> Path:
    if kind not in {"manifests", "revisions"} or not _valid_id(object_id):
        raise HistoryError("Invalid history object identifier")
    return history / kind / ("%s.json" % object_id)


def _store_immutable(history: Path, path: Path, data: bytes) -> None:
    _ensure_internal_write_parent(history, path)
    if path.exists() or path.is_symlink():
        existing = _read_internal(history, path)
        if existing != data:
            raise HistoryError(
                "Immutable history object has conflicting content: %s" % path
            )
        return
    _atomic_write(path, data, mode=0o600)


def _store_blob(history: Path, blob_id: str, data: bytes) -> None:
    if _digest(data) != blob_id:
        raise HistoryError("Blob identifier does not match content")
    _store_immutable(history, _blob_path(history, blob_id), data)


def _store_json_object(history: Path, kind: str, payload: Mapping[str, Any]) -> str:
    canonical = _canonical_bytes(payload)
    object_id = _digest(canonical)
    _store_immutable(
        history,
        _object_path(history, kind, object_id),
        canonical + b"\n",
    )
    return object_id


def _load_json_object(history: Path, kind: str, object_id: str) -> Dict[str, Any]:
    path = _object_path(history, kind, object_id)
    payload = _load_json(history, path)
    if not isinstance(payload, dict):
        raise HistoryError("History object must be a JSON object: %s" % path)
    if _digest(_canonical_bytes(payload)) != object_id:
        raise HistoryError("History object hash mismatch: %s" % path)
    return payload


def _load_format(history: Path) -> Dict[str, Any]:
    payload = _load_json(history, history / "format.json")
    if not isinstance(payload, dict):
        raise HistoryError("Invalid portable history format")
    expected_keys = {
        "format",
        "version",
        "hash",
        "brain_id",
        "created_at",
    }
    if (
        set(payload) != expected_keys
        or payload.get("format") != FORMAT_NAME
        or payload.get("version") != FORMAT_VERSION
        or payload.get("hash") != HASH_NAME
        or not isinstance(payload.get("brain_id"), str)
        or not payload.get("brain_id")
        or not isinstance(payload.get("created_at"), str)
        or not payload.get("created_at")
    ):
        raise HistoryError("Unsupported or corrupt portable history format")
    try:
        parsed_brain_id = uuid.UUID(payload["brain_id"])
        parsed_created_at = dt.datetime.fromisoformat(payload["created_at"])
    except (ValueError, TypeError) as exc:
        raise HistoryError("Corrupt portable history identity metadata") from exc
    if (
        str(parsed_brain_id) != payload["brain_id"]
        or parsed_created_at.tzinfo is None
        or parsed_created_at.utcoffset() != dt.timedelta(0)
    ):
        raise HistoryError("Corrupt portable history identity metadata")
    return payload


def _read_head(history: Path) -> str:
    data = _read_internal(history, history / "HEAD")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise HistoryError("Corrupt portable history HEAD") from exc
    if not text.endswith("\n") or text.count("\n") != 1:
        raise HistoryError("Corrupt portable history HEAD")
    revision_id = text[:-1]
    if not _valid_id(revision_id):
        raise HistoryError("Corrupt portable history HEAD")
    return revision_id


def _write_head(history: Path, revision_id: str) -> None:
    if not _valid_id(revision_id):
        raise HistoryError("Invalid revision identifier for HEAD")
    _atomic_write(history / "HEAD", (revision_id + "\n").encode("ascii"), mode=0o600)


def _validate_manifest(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if (
        set(payload) != {"version", "entries"}
        or payload.get("version") != FORMAT_VERSION
        or not isinstance(payload.get("entries"), dict)
    ):
        raise HistoryError("Corrupt manifest structure")
    normalized: Dict[str, Dict[str, Any]] = {}
    for raw_path, raw_entry in payload["entries"].items():
        if not isinstance(raw_path, str):
            raise HistoryError("Manifest path must be text")
        path = _validate_relative(raw_path)
        if (
            path != raw_path
            or not isinstance(raw_entry, dict)
            or set(raw_entry) != {"blob", "size", "mode"}
        ):
            raise HistoryError("Corrupt manifest entry: %s" % raw_path)
        blob_id = raw_entry.get("blob")
        size = raw_entry.get("size")
        mode = raw_entry.get("mode")
        if (
            not _valid_id(blob_id)
            or type(size) is not int
            or size < 0
            or type(mode) is not int
            or mode < 0
            or mode > 0o7777
        ):
            raise HistoryError("Corrupt manifest metadata: %s" % raw_path)
        normalized[path] = {"blob": blob_id, "size": size, "mode": mode}
    return dict(sorted(normalized.items()))


def _validate_revision(payload: Dict[str, Any]) -> None:
    expected_keys = {
        "version",
        "parent",
        "manifest",
        "created_at",
        "kind",
        "summary",
        "reason",
        "changed_paths",
        "undo_target",
    }
    required_text = ("manifest", "created_at", "kind", "summary", "reason")
    if set(payload) != expected_keys or payload.get("version") != FORMAT_VERSION:
        raise HistoryError("Corrupt revision version")
    if not all(
        isinstance(payload.get(key), str) and payload.get(key) for key in required_text
    ):
        raise HistoryError("Corrupt revision metadata")
    if not _valid_id(payload["manifest"]):
        raise HistoryError("Corrupt revision manifest reference")
    parent = payload.get("parent")
    if parent is not None and not _valid_id(parent):
        raise HistoryError("Corrupt revision parent")
    if payload["kind"] not in {"init", "save", "undo"}:
        raise HistoryError("Corrupt revision kind")
    changed = payload.get("changed_paths")
    if not isinstance(changed, list):
        raise HistoryError("Corrupt revision changed_paths")
    normalized = [_validate_relative(path) for path in changed]
    if normalized != sorted(set(normalized)):
        raise HistoryError("Revision changed_paths must be sorted and unique")
    undo_target = payload.get("undo_target")
    if undo_target is not None and not _valid_id(undo_target):
        raise HistoryError("Corrupt revision undo target")
    if payload["kind"] == "undo" and undo_target is None:
        raise HistoryError("Undo revision lacks undo target")
    if payload["kind"] != "undo" and undo_target is not None:
        raise HistoryError("Non-undo revision has an undo target")
    try:
        created_at = dt.datetime.fromisoformat(payload["created_at"])
    except (ValueError, TypeError) as exc:
        raise HistoryError("Corrupt revision timestamp") from exc
    if created_at.tzinfo is None or created_at.utcoffset() != dt.timedelta(0):
        raise HistoryError("Corrupt revision timestamp")


def _load_manifest(history: Path, manifest_id: str) -> Dict[str, Dict[str, Any]]:
    return _validate_manifest(_load_json_object(history, "manifests", manifest_id))


def _load_revision(history: Path, revision_id: str) -> Dict[str, Any]:
    payload = _load_json_object(history, "revisions", revision_id)
    _validate_revision(payload)
    return payload


def _verify_blob(history: Path, blob_id: str, expected_size: int) -> bytes:
    path = _blob_path(history, blob_id)
    data = _read_internal(history, path)
    if len(data) != expected_size or _digest(data) != blob_id:
        raise HistoryError("Corrupt blob: %s" % blob_id)
    return data


def _load_chain(
    history: Path,
) -> Tuple[str, List[Tuple[str, Dict[str, Any]]], Dict[str, Dict[str, Dict[str, Any]]]]:
    head = _read_head(history)
    chain: List[Tuple[str, Dict[str, Any]]] = []
    manifests: Dict[str, Dict[str, Dict[str, Any]]] = {}
    seen_revisions: Set[str] = set()
    verified_blobs: Set[str] = set()
    current: Optional[str] = head
    while current is not None:
        if current in seen_revisions:
            raise HistoryError("Revision parent cycle detected")
        seen_revisions.add(current)
        revision = _load_revision(history, current)
        manifest_id = revision["manifest"]
        if manifest_id not in manifests:
            entries = _load_manifest(history, manifest_id)
            for entry in entries.values():
                blob_id = entry["blob"]
                if blob_id not in verified_blobs:
                    _verify_blob(history, blob_id, entry["size"])
                    verified_blobs.add(blob_id)
            manifests[manifest_id] = entries
        chain.append((current, revision))
        current = revision.get("parent")
    if not chain or chain[-1][1].get("parent") is not None:
        raise HistoryError("Revision chain has no root")
    revision_positions = {
        revision_id: index for index, (revision_id, _revision) in enumerate(chain)
    }
    revisions = {revision_id: revision for revision_id, revision in chain}
    for index, (revision_id, revision) in enumerate(chain):
        entries = manifests[revision["manifest"]]
        parent_id = revision.get("parent")
        if parent_id is None:
            if index != len(chain) - 1 or revision["kind"] != "init":
                raise HistoryError("Revision chain has an invalid root revision")
            actual_changed = sorted(entries)
        else:
            if revision["kind"] == "init":
                raise HistoryError("Only the root revision may be an initialization")
            parent_entries = manifests[revisions[parent_id]["manifest"]]
            actual_changed = _flatten_changes(_diff_entries(parent_entries, entries))
            if not actual_changed:
                raise HistoryError("Non-initial revision contains no changes")
        if revision["changed_paths"] != actual_changed:
            raise HistoryError(
                "Revision changed_paths do not match its manifests: %s" % revision_id
            )
        if revision["kind"] == "undo":
            target = revision["undo_target"]
            if (
                target not in revision_positions
                or revision_positions[target] <= index
                or revisions[target]["manifest"] != revision["manifest"]
            ):
                raise HistoryError("Undo revision has an invalid target")
    return head, chain, manifests


def _manifest_payload(entries: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "version": FORMAT_VERSION,
        "entries": {
            path: {
                "blob": entry["blob"],
                "size": entry["size"],
                "mode": entry["mode"],
            }
            for path, entry in sorted(entries.items())
        },
    }


def _revision_payload(
    *,
    parent: Optional[str],
    manifest_id: str,
    kind: str,
    summary: str,
    reason: str,
    changed_paths: Iterable[str],
    undo_target: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "version": FORMAT_VERSION,
        "parent": parent,
        "manifest": manifest_id,
        "created_at": _now(),
        "kind": kind,
        "summary": _require_text(summary, "summary"),
        "reason": _require_text(reason, "reason"),
        "changed_paths": sorted(set(changed_paths)),
        "undo_target": undo_target,
    }


def _diff_entries(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> Dict[str, List[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return {
        "added": sorted(after_paths - before_paths),
        "modified": sorted(
            path
            for path in before_paths & after_paths
            if dict(before[path]) != dict(after[path])
        ),
        "deleted": sorted(before_paths - after_paths),
    }


def _flatten_changes(changes: Mapping[str, Iterable[str]]) -> List[str]:
    combined: Set[str] = set()
    for key in ("added", "modified", "deleted"):
        combined.update(changes.get(key, []))
    return sorted(combined)


def _check_pending(history: Path, *, allow_lock: bool = False) -> None:
    recovery = history / "RECOVERY_REQUIRED"
    if recovery.exists() or recovery.is_symlink():
        raise HistoryError("Portable history requires manual recovery")
    lock = history / "LOCK"
    if (lock.exists() or lock.is_symlink()) and not allow_lock:
        raise HistoryError("Portable history is locked by another operation")
    transactions = history / "transactions"
    if transactions.exists() or transactions.is_symlink():
        metadata = transactions.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise HistoryError("Portable history transactions directory is unsafe")
        try:
            pending = [path for path in transactions.iterdir()]
        except OSError as exc:
            raise HistoryError("Cannot inspect portable history transactions") from exc
        if pending:
            raise HistoryError("Portable history has an incomplete transaction")


def _open_history(
    root: Path, *, check_pending: bool = True
) -> Tuple[Path, Dict[str, Any]]:
    history = _history_path(root)
    _assert_history_directory(history)
    format_payload = _load_format(history)
    if check_pending:
        _check_pending(history)
    return history, format_payload


@contextlib.contextmanager
def _exclusive_lock(history: Path) -> Iterator[None]:
    lock_path = history / "LOCK"
    payload = (
        _canonical_bytes(
            {
                "version": FORMAT_VERSION,
                "pid": os.getpid(),
                "host": platform.node() or "unknown-local-host",
                "created_at": _now(),
                "nonce": uuid.uuid4().hex,
            }
        )
        + b"\n"
    )
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(str(lock_path), flags, PRIVATE_FILE_MODE)
    except FileExistsError as exc:
        raise HistoryError("Portable history is locked by another operation") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(history)
        yield
    finally:
        try:
            metadata = lock_path.lstat()
            if (
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_nlink == 1
                and _read_internal(history, lock_path) == payload
            ):
                lock_path.unlink()
                _fsync_directory(history)
        except (OSError, HistoryError):
            pass


def _inspect_unlocked(
    root: Path,
    history: Path,
) -> Tuple[
    Dict[str, Any],
    Dict[str, bytes],
    Dict[str, Dict[str, Any]],
    List[Tuple[str, Dict[str, Any]]],
]:
    head, chain, manifests = _load_chain(history)
    head_revision = chain[0][1]
    head_entries = manifests[head_revision["manifest"]]
    current_entries, current_blobs = _scan_tree(root)
    changes = _diff_entries(head_entries, current_entries)
    result = {
        "integrity": "ok",
        "head": head,
        "clean": not _flatten_changes(changes),
        "added": changes["added"],
        "modified": changes["modified"],
        "deleted": changes["deleted"],
    }
    return result, current_blobs, current_entries, chain


def initialize(root: Any, summary: str, reason: str) -> str:
    """Create portable history for an existing brain tree.

    Repeating initialization against a healthy, clean history is a no-op.
    """

    brain_root = _normalize_root(root)
    summary = _require_text(summary, "summary")
    reason = _require_text(reason, "reason")
    history = _history_path(brain_root)
    if history.exists() or history.is_symlink():
        result = inspect(brain_root)
        if not result["clean"]:
            raise HistoryError("Portable history already exists with unsaved changes")
        return result["head"]

    entries, blobs = _scan_tree(brain_root)
    staging = brain_root / (".brain-history-init-%s" % uuid.uuid4().hex)
    try:
        staging.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        for relative in (
            "blobs",
            "blobs/%s" % HASH_NAME,
            "manifests",
            "revisions",
            "transactions",
            "tmp",
        ):
            directory = staging / relative
            if not directory.exists():
                directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        _write_json(
            staging / "format.json",
            {
                "format": FORMAT_NAME,
                "version": FORMAT_VERSION,
                "hash": HASH_NAME,
                "brain_id": str(uuid.uuid4()),
                "created_at": _now(),
            },
        )
        for blob_id, data in sorted(blobs.items()):
            _store_blob(staging, blob_id, data)
        manifest_id = _store_json_object(
            staging,
            "manifests",
            _manifest_payload(entries),
        )
        revision_id = _store_json_object(
            staging,
            "revisions",
            _revision_payload(
                parent=None,
                manifest_id=manifest_id,
                kind="init",
                summary=summary,
                reason=reason,
                changed_paths=entries.keys(),
            ),
        )
        _write_head(staging, revision_id)
        _load_format(staging)
        _load_chain(staging)
        try:
            os.rename(str(staging), str(history))
        except OSError:
            if history.exists():
                if staging.exists():
                    shutil.rmtree(str(staging))
                result = inspect(brain_root)
                if not result["clean"]:
                    raise HistoryError(
                        "Concurrent initialization produced history with unsaved changes"
                    )
                return result["head"]
            raise
        _fsync_directory(brain_root)
        return revision_id
    finally:
        if staging.exists():
            shutil.rmtree(str(staging))


def inspect(root: Any) -> Dict[str, Any]:
    """Verify reachable history and compare the working tree with HEAD."""

    brain_root = _normalize_root(root)
    history, _format_payload = _open_history(brain_root)
    result, _blobs, _entries, _chain = _inspect_unlocked(brain_root, history)
    return result


def changed_paths(root: Any) -> List[str]:
    """Return the sorted union of added, modified, and deleted tracked paths."""

    result = inspect(root)
    return sorted(set(result["added"] + result["modified"] + result["deleted"]))


def _normalize_expected_paths(expected_paths: Iterable[Any]) -> List[str]:
    if isinstance(expected_paths, (str, bytes, os.PathLike)):
        raise HistoryError("Expected paths must be an iterable of individual paths")
    try:
        raw_values = list(expected_paths)
    except TypeError as exc:
        raise HistoryError("Expected paths must be iterable") from exc
    normalized = [_validate_relative(value) for value in raw_values]
    if len(normalized) != len(set(normalized)):
        raise HistoryError("Expected path set contains duplicates")
    return sorted(normalized)


def save(
    root: Any,
    expected_paths: Iterable[Any],
    summary: str,
    reason: str,
) -> str:
    """Save exactly the current working-tree delta as one forward revision."""

    brain_root = _normalize_root(root)
    summary = _require_text(summary, "summary")
    reason = _require_text(reason, "reason")
    expected = _normalize_expected_paths(expected_paths)
    history, _format_payload = _open_history(brain_root)
    with _exclusive_lock(history):
        _check_pending(history, allow_lock=True)
        result, blobs, current_entries, chain = _inspect_unlocked(brain_root, history)
        actual = sorted(set(result["added"] + result["modified"] + result["deleted"]))
        if actual != expected:
            raise HistoryError(
                "Changed paths do not match expected set; expected=%r actual=%r"
                % (expected, actual)
            )
        if not actual:
            raise HistoryError("No changes to save")
        for blob_id, data in sorted(blobs.items()):
            _store_blob(history, blob_id, data)
        manifest_id = _store_json_object(
            history,
            "manifests",
            _manifest_payload(current_entries),
        )
        parent = result["head"]
        revision_id = _store_json_object(
            history,
            "revisions",
            _revision_payload(
                parent=parent,
                manifest_id=manifest_id,
                kind="save",
                summary=summary,
                reason=reason,
                changed_paths=actual,
            ),
        )
        _write_head(history, revision_id)
        return revision_id


def list_revisions(root: Any) -> List[Dict[str, Any]]:
    """Return reachable revisions from newest to oldest."""

    brain_root = _normalize_root(root)
    history, _format_payload = _open_history(brain_root)
    _head, chain, _manifests = _load_chain(history)
    return [{"id": revision_id, **revision} for revision_id, revision in chain]


def _undo_preview_unlocked(
    root: Path,
    history: Path,
    format_payload: Mapping[str, Any],
    to_revision: str,
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    if not _valid_id(to_revision):
        raise HistoryError("Invalid target revision")
    result, _blobs, current_entries, chain = _inspect_unlocked(root, history)
    if not result["clean"]:
        raise HistoryError("Undo requires a clean working tree")
    revisions = {revision_id: revision for revision_id, revision in chain}
    if to_revision not in revisions:
        raise HistoryError("Undo target is not reachable from current history")
    current_revision = revisions[result["head"]]
    target_revision = revisions[to_revision]
    current_entries = _load_manifest(history, current_revision["manifest"])
    target_entries = _load_manifest(history, target_revision["manifest"])
    changes = _diff_entries(current_entries, target_entries)
    if not _flatten_changes(changes):
        raise HistoryError("Working tree already matches target revision")
    token_payload = {
        "version": FORMAT_VERSION,
        "brain_id": format_payload["brain_id"],
        "from_revision": result["head"],
        "to_revision": to_revision,
        "changes": changes,
    }
    preview = {
        "token": _digest(_canonical_bytes(token_payload)),
        "from_revision": result["head"],
        "to_revision": to_revision,
        "changes": changes,
    }
    return preview, current_entries, target_entries


def undo_preview(root: Any, to_revision: str) -> Dict[str, Any]:
    """Preview a forward undo revision and return a confirmation token."""

    brain_root = _normalize_root(root)
    history, format_payload = _open_history(brain_root)
    preview, _current, _target = _undo_preview_unlocked(
        brain_root,
        history,
        format_payload,
        to_revision,
    )
    return preview


def _ensure_safe_parent(root: Path, target: Path) -> None:
    try:
        relative = target.parent.relative_to(root)
    except ValueError as exc:
        raise HistoryError("Undo target escapes the brain root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise HistoryError("Unsafe undo parent path: %s" % current)
        else:
            current.mkdir()


def _remove_empty_directory_tree(path: Path) -> None:
    """Remove a directory-shaped conflict only when it contains no files."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise HistoryError("Unsafe directory conflict during undo: %s" % path)

    directories: List[Path] = []

    def collect(directory: Path) -> None:
        try:
            children = list(os.scandir(str(directory)))
        except OSError as exc:
            raise HistoryError(
                "Cannot inspect directory conflict during undo: %s" % directory
            ) from exc
        for child in children:
            child_path = directory / child.name
            try:
                child_metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise HistoryError(
                    "Cannot inspect directory conflict during undo: %s" % child_path
                ) from exc
            if stat.S_ISLNK(child_metadata.st_mode):
                raise HistoryError(
                    "Directory conflict contains a symlink: %s" % child_path
                )
            if not stat.S_ISDIR(child_metadata.st_mode):
                raise HistoryError(
                    "Directory conflict contains an untracked file: %s" % child_path
                )
            collect(child_path)
        directories.append(directory)

    collect(path)
    for directory in directories:
        try:
            directory.rmdir()
        except OSError as exc:
            if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                raise HistoryError(
                    "Directory conflict changed during undo: %s" % directory
                ) from exc
            raise HistoryError(
                "Cannot remove empty directory conflict during undo: %s" % directory
            ) from exc


def _remove_transaction_path(path: Path, *, allow_empty_directory: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise HistoryError("Unsafe path encountered during undo: %s" % path)
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
        path.unlink()
        return
    if stat.S_ISDIR(metadata.st_mode) and allow_empty_directory:
        _remove_empty_directory_tree(path)
        return
    raise HistoryError("Unsafe path encountered during undo: %s" % path)


def _materialize_entry(
    root: Path,
    history: Path,
    relative: str,
    entry: Mapping[str, Any],
) -> None:
    target = _path_for(root, relative)
    _ensure_safe_parent(root, target)
    if target.exists() or target.is_symlink():
        metadata = target.lstat()
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            _remove_empty_directory_tree(target)
        else:
            raise HistoryError("Unsafe pre-existing undo target: %s" % target)
    data = _verify_blob(history, entry["blob"], entry["size"])
    _atomic_write(target, data, mode=entry["mode"])


def _write_recovery_marker(history: Path, message: str) -> None:
    try:
        _write_json(
            history / "RECOVERY_REQUIRED",
            {"at": _now(), "message": message},
        )
    except Exception:
        pass


def _load_private_json(
    history: Path,
    path: Path,
) -> Tuple[Dict[str, Any], bytes]:
    _private_regular_metadata(path)
    data = _read_internal(history, path)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoryError("Corrupt private history JSON: %s" % path) from exc
    if not isinstance(payload, dict):
        raise HistoryError("Private history JSON must be an object: %s" % path)
    if data != _canonical_bytes(payload) + b"\n":
        raise HistoryError("Non-canonical private history JSON: %s" % path)
    return payload, data


def _load_stale_lock(
    history: Path,
    path: Path,
    *,
    label: str,
) -> Optional[Dict[str, Any]]:
    if not path.exists() and not path.is_symlink():
        return None
    payload, data = _load_private_json(history, path)
    legacy_keys = {"version", "pid", "created_at", "nonce"}
    current_keys = legacy_keys | {"host"}
    if frozenset(payload) not in {frozenset(legacy_keys), frozenset(current_keys)}:
        raise HistoryError("%s has an unexpected schema" % label)
    nonce = payload.get("nonce")
    if (
        payload.get("version") != FORMAT_VERSION
        or type(payload.get("pid")) is not int
        or payload["pid"] <= 0
        or not _valid_utc_timestamp(payload.get("created_at"))
        or not isinstance(nonce, str)
        or len(nonce) != 32
        or any(character not in "0123456789abcdef" for character in nonce)
    ):
        raise HistoryError("%s has invalid identity metadata" % label)
    current_host = platform.node() or "unknown-local-host"
    recorded_host = payload.get("host")
    if recorded_host is not None and (
        not isinstance(recorded_host, str) or not recorded_host.strip()
    ):
        raise HistoryError("%s has an invalid host" % label)
    if recorded_host is not None and recorded_host != current_host:
        raise HistoryError(
            "%s may belong to a process on another host; recovery is ambiguous"
            % label
        )
    if _pid_is_alive(payload["pid"]):
        raise HistoryError("%s belongs to an active local process" % label)
    return {
        "path": path,
        "payload": payload,
        "bytes": data,
        "sha256": _digest(data),
    }


def _load_recovery_marker(
    history: Path,
) -> Optional[Dict[str, Any]]:
    path = history / "RECOVERY_REQUIRED"
    if not path.exists() and not path.is_symlink():
        return None
    payload, data = _load_private_json(history, path)
    if (
        set(payload) != {"at", "message"}
        or not _valid_utc_timestamp(payload.get("at"))
        or not isinstance(payload.get("message"), str)
        or not payload["message"].strip()
    ):
        raise HistoryError("Portable history recovery marker is invalid")
    return {
        "path": path,
        "payload": payload,
        "bytes": data,
        "sha256": _digest(data),
    }


def _validate_change_map(raw_changes: Any) -> Dict[str, List[str]]:
    if not isinstance(raw_changes, dict) or set(raw_changes) != {
        "added",
        "modified",
        "deleted",
    }:
        raise HistoryError("Undo recovery journal has invalid changes")
    normalized: Dict[str, List[str]] = {}
    seen: Set[str] = set()
    for kind in ("added", "modified", "deleted"):
        values = raw_changes[kind]
        if not isinstance(values, list):
            raise HistoryError("Undo recovery journal change lists are invalid")
        paths = [_validate_relative(value) for value in values]
        if paths != sorted(set(paths)):
            raise HistoryError(
                "Undo recovery journal change lists must be sorted and unique"
            )
        if seen.intersection(paths):
            raise HistoryError("Undo recovery journal change paths overlap")
        seen.update(paths)
        normalized[kind] = paths
    if not seen:
        raise HistoryError("Undo recovery journal contains no changes")
    return normalized


def _load_undo_journal(
    history: Path,
    path: Path,
) -> Tuple[Dict[str, Any], bytes]:
    payload, data = _load_private_json(history, path)
    if set(payload) != {
        "version",
        "state",
        "from_revision",
        "to_revision",
        "token",
        "changes",
        "created_at",
    }:
        raise HistoryError("Undo recovery journal has an unexpected schema")
    if (
        payload.get("version") != FORMAT_VERSION
        or payload.get("state") != "prepared"
        or not _valid_id(payload.get("from_revision"))
        or not _valid_id(payload.get("to_revision"))
        or payload["from_revision"] == payload["to_revision"]
        or not _valid_id(payload.get("token"))
        or not _valid_utc_timestamp(payload.get("created_at"))
    ):
        raise HistoryError("Undo recovery journal has invalid identity metadata")
    normalized = dict(payload)
    normalized["changes"] = _validate_change_map(payload.get("changes"))
    if normalized != payload:
        raise HistoryError("Undo recovery journal is not normalized")
    return normalized, data


def _scan_private_quarantine(
    quarantine: Path,
) -> Dict[str, Dict[str, Any]]:
    entries: Dict[str, Dict[str, Any]] = {}
    _private_directory_metadata(quarantine)

    def walk(directory: Path, relative_parts: Tuple[str, ...]) -> None:
        _private_directory_metadata(directory)
        try:
            children = sorted(os.scandir(str(directory)), key=lambda item: item.name)
        except OSError as exc:
            raise HistoryError(
                "Cannot enumerate undo recovery quarantine: %s" % directory
            ) from exc
        for child in children:
            path = directory / child.name
            child_parts = relative_parts + (child.name,)
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise HistoryError(
                    "Cannot inspect undo recovery quarantine: %s" % path
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise HistoryError(
                    "Undo recovery quarantine contains a symlink: %s" % path
                )
            if stat.S_ISDIR(metadata.st_mode):
                walk(path, child_parts)
                continue
            _private_regular_metadata(path)
            data, mode = _safe_read_regular(path)
            if mode != PRIVATE_FILE_MODE:
                raise HistoryError(
                    "Undo recovery quarantine file mode is not private: %s" % path
                )
            relative = _validate_relative("/".join(child_parts))
            entries[relative] = {
                "blob": _digest(data),
                "size": len(data),
                "mode": mode,
            }

    walk(quarantine, ())
    return dict(sorted(entries.items()))


def _load_pending_undo(
    history: Path,
) -> Optional[Dict[str, Any]]:
    transactions = history / "transactions"
    _private_directory_metadata(transactions)
    try:
        children = sorted(os.scandir(str(transactions)), key=lambda item: item.name)
    except OSError as exc:
        raise HistoryError("Cannot inspect portable history transactions") from exc
    if len(children) > 1:
        raise HistoryError(
            "Multiple incomplete history transactions make recovery ambiguous"
        )
    if not children:
        return None
    child = children[0]
    transaction = transactions / child.name
    if (
        len(child.name) != 32
        or any(character not in "0123456789abcdef" for character in child.name)
    ):
        raise HistoryError("Incomplete history transaction has an invalid name")
    _private_directory_metadata(transaction)
    try:
        transaction_children = {
            item.name: item for item in os.scandir(str(transaction))
        }
    except OSError as exc:
        raise HistoryError(
            "Cannot inspect incomplete history transaction"
        ) from exc
    if set(transaction_children) != {"journal.json", "quarantine"}:
        raise HistoryError(
            "Incomplete history transaction has unexpected contents"
        )
    journal_path = transaction / "journal.json"
    quarantine = transaction / "quarantine"
    journal, journal_bytes = _load_undo_journal(history, journal_path)
    quarantine_entries = _scan_private_quarantine(quarantine)
    return {
        "path": transaction,
        "name": child.name,
        "journal": journal,
        "journal_bytes": journal_bytes,
        "journal_sha256": _digest(journal_bytes),
        "quarantine": quarantine,
        "quarantine_entries": quarantine_entries,
    }


def _rollback_undo(
    root: Path,
    transaction: Path,
    changed: Iterable[str],
    current_entries: Mapping[str, Mapping[str, Any]],
) -> None:
    quarantine = transaction / "quarantine"
    changed_set = set(changed)
    saved_paths: List[str] = []
    for relative in changed_set & set(current_entries):
        saved = quarantine.joinpath(*relative.split("/"))
        if saved.exists() or saved.is_symlink():
            metadata = saved.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise HistoryError(
                    "Undo quarantine contains an unsafe path: %s" % saved
                )
            saved_paths.append(relative)

    # First remove target-only files that may already have been materialized.
    # A directory at such a path belongs to the old tree (or is only a parent
    # for another materialized file), so it is left for the saved-path pass.
    target_only = changed_set - set(current_entries)
    for relative in sorted(
        target_only,
        key=lambda value: (value.count("/"), value),
        reverse=True,
    ):
        target = _path_for(root, relative)
        if target.exists() or target.is_symlink():
            metadata = target.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                _remove_transaction_path(
                    target,
                    allow_empty_directory=False,
                )

    # Then remove target versions of files that were successfully quarantined.
    # This also clears empty directories created by a file-to-directory undo.
    for relative in sorted(
        saved_paths,
        key=lambda value: (value.count("/"), value),
        reverse=True,
    ):
        target = _path_for(root, relative)
        if target.exists() or target.is_symlink():
            _remove_transaction_path(
                target,
                allow_empty_directory=True,
            )

    # Finally restore every quarantined original, creating parent directories
    # only after conflicting target paths have been removed.
    for relative in sorted(
        saved_paths,
        key=lambda value: (value.count("/"), value),
    ):
        target = _path_for(root, relative)
        saved = quarantine.joinpath(*relative.split("/"))
        _ensure_safe_parent(root, target)
        os.replace(str(saved), str(target))
        entry = current_entries[relative]
        try:
            os.chmod(str(target), entry["mode"])
        except OSError:
            pass
    restored, _blobs = _scan_tree(root)
    if restored != dict(sorted(current_entries.items())):
        raise HistoryError("Undo rollback could not restore the previous manifest")


def _manifest_digest(entries: Mapping[str, Mapping[str, Any]]) -> str:
    return _digest(_canonical_bytes(_manifest_payload(entries)))


def _validate_partial_undo_tree(
    live_entries: Mapping[str, Mapping[str, Any]],
    current_entries: Mapping[str, Mapping[str, Any]],
    target_entries: Mapping[str, Mapping[str, Any]],
    quarantine_entries: Mapping[str, Mapping[str, Any]],
    changed: Iterable[str],
) -> None:
    changed_set = set(changed)
    allowed_quarantine = changed_set & set(current_entries)
    if not set(quarantine_entries).issubset(allowed_quarantine):
        raise HistoryError(
            "Undo recovery quarantine contains an unexpected tracked path"
        )
    for relative, saved in quarantine_entries.items():
        expected = current_entries[relative]
        if (
            saved.get("blob") != expected.get("blob")
            or saved.get("size") != expected.get("size")
        ):
            raise HistoryError(
                "Undo recovery quarantine does not match the prior revision: %s"
                % relative
            )

    live_outside = {
        path: dict(entry)
        for path, entry in live_entries.items()
        if path not in changed_set
    }
    current_outside = {
        path: dict(entry)
        for path, entry in current_entries.items()
        if path not in changed_set
    }
    if live_outside != current_outside:
        raise HistoryError(
            "Working tree changed outside the incomplete undo transaction"
        )

    for relative in sorted(changed_set):
        live = live_entries.get(relative)
        current = current_entries.get(relative)
        target = target_entries.get(relative)
        quarantined = relative in quarantine_entries
        if current is not None:
            if quarantined:
                if live is not None and (
                    target is None or dict(live) != dict(target)
                ):
                    raise HistoryError(
                        "Incomplete undo has an ambiguous materialized path: %s"
                        % relative
                    )
            elif live is None or dict(live) != dict(current):
                raise HistoryError(
                    "Incomplete undo lost an unquarantined prior path: %s"
                    % relative
                )
        else:
            if quarantined:
                raise HistoryError(
                    "Undo quarantine contains a path absent from the prior revision"
                )
            if live is not None and (
                target is None or dict(live) != dict(target)
            ):
                raise HistoryError(
                    "Incomplete undo has an ambiguous added path: %s" % relative
                )


def _recovery_plan(
    root: Path,
    *,
    inspect_recovery_lock: bool,
) -> Dict[str, Any]:
    history, format_payload = _open_history(root, check_pending=False)
    lock = _load_stale_lock(history, history / "LOCK", label="Portable history lock")
    recovery_lock = (
        _load_stale_lock(
            history,
            history / "RECOVERY_LOCK",
            label="Portable history recovery lock",
        )
        if inspect_recovery_lock
        else None
    )
    marker = _load_recovery_marker(history)
    pending = _load_pending_undo(history)
    head, chain, manifests = _load_chain(history)
    revisions = {revision_id: revision for revision_id, revision in chain}
    positions = {
        revision_id: index for index, (revision_id, _revision) in enumerate(chain)
    }
    live_entries, _live_blobs = _scan_tree(root)
    head_entries = manifests[revisions[head]["manifest"]]
    clean = live_entries == dict(sorted(head_entries.items()))

    action: Optional[str] = None
    changes: Optional[Dict[str, List[str]]] = None
    current_entries: Optional[Dict[str, Dict[str, Any]]] = None
    target_entries: Optional[Dict[str, Dict[str, Any]]] = None

    if pending is not None:
        journal = pending["journal"]
        from_revision = journal["from_revision"]
        to_revision = journal["to_revision"]
        if from_revision not in revisions or to_revision not in revisions:
            raise HistoryError(
                "Undo recovery journal references an unreachable revision"
            )
        if positions[to_revision] <= positions[from_revision]:
            raise HistoryError(
                "Undo recovery target is not older than its source revision"
            )
        from_payload = revisions[from_revision]
        to_payload = revisions[to_revision]
        current_entries = manifests[from_payload["manifest"]]
        target_entries = manifests[to_payload["manifest"]]
        changes = _diff_entries(current_entries, target_entries)
        if journal["changes"] != changes:
            raise HistoryError(
                "Undo recovery journal changes disagree with revision manifests"
            )
        expected_token = _digest(
            _canonical_bytes(
                {
                    "version": FORMAT_VERSION,
                    "brain_id": format_payload["brain_id"],
                    "from_revision": from_revision,
                    "to_revision": to_revision,
                    "changes": changes,
                }
            )
        )
        if not hmac.compare_digest(journal["token"], expected_token):
            raise HistoryError("Undo recovery journal confirmation token is invalid")
        changed = _flatten_changes(changes)
        _validate_partial_undo_tree(
            live_entries,
            current_entries,
            target_entries,
            pending["quarantine_entries"],
            changed,
        )
        if head == from_revision:
            action = "rollback-unpublished-undo"
        else:
            head_payload = revisions[head]
            if (
                head_payload.get("kind") != "undo"
                or head_payload.get("parent") != from_revision
                or head_payload.get("undo_target") != to_revision
                or head_payload.get("manifest") != to_payload.get("manifest")
                or head_payload.get("changed_paths") != changed
                or live_entries != dict(sorted(target_entries.items()))
            ):
                raise HistoryError(
                    "Published history does not match the incomplete undo journal"
                )
            action = "cleanup-published-undo"
    elif marker is not None:
        if not clean:
            raise HistoryError(
                "Recovery marker exists but the working tree is not a verified HEAD"
            )
        action = "clear-recovered-state"
    elif lock is not None:
        action = "clear-stale-lock"

    public: Dict[str, Any] = {
        "needed": action is not None,
        "action": action,
        "head": head,
        "clean": clean,
        "transaction": pending["name"] if pending is not None else None,
        "changes": changes,
        "stale_lock": lock is not None,
        "recovery_marker": marker is not None,
    }
    main_payload = {
        "version": FORMAT_VERSION,
        "brain_id": format_payload["brain_id"],
        "action": action,
        "head": head,
        "clean": clean,
        "live_manifest_sha256": _manifest_digest(live_entries),
        "lock_sha256": lock["sha256"] if lock is not None else None,
        "marker_sha256": marker["sha256"] if marker is not None else None,
        "transaction": pending["name"] if pending is not None else None,
        "journal_sha256": (
            pending["journal_sha256"] if pending is not None else None
        ),
        "quarantine_manifest_sha256": (
            _manifest_digest(pending["quarantine_entries"])
            if pending is not None
            else None
        ),
    }
    main_digest = _digest(_canonical_bytes(main_payload))
    token_payload = {
        "version": FORMAT_VERSION,
        "main_plan_sha256": main_digest,
        "recovery_lock_sha256": (
            recovery_lock["sha256"] if recovery_lock is not None else None
        ),
    }
    token = _digest(_canonical_bytes(token_payload)) if action is not None else None
    public["token"] = token
    return {
        "history": history,
        "format": format_payload,
        "public": public,
        "main_digest": main_digest,
        "token": token,
        "lock": lock,
        "recovery_lock": recovery_lock,
        "marker": marker,
        "pending": pending,
        "current_entries": current_entries,
        "target_entries": target_entries,
    }


def recovery_preview(root: Any) -> Dict[str, Any]:
    """Preview exact fail-closed recovery of stale history operation state."""

    brain_root = _normalize_root(root)
    plan = _recovery_plan(brain_root, inspect_recovery_lock=True)
    return dict(plan["public"])


@contextlib.contextmanager
def _exclusive_recovery_lock(
    history: Path,
    expected_stale: Optional[Mapping[str, Any]],
) -> Iterator[None]:
    path = history / "RECOVERY_LOCK"
    if expected_stale is not None:
        current = _load_stale_lock(
            history,
            path,
            label="Portable history recovery lock",
        )
        if current is None or current["bytes"] != expected_stale["bytes"]:
            raise HistoryError("Portable history recovery lock changed after preview")
        path.unlink()
        _fsync_directory(history)
    elif path.exists() or path.is_symlink():
        raise HistoryError("Portable history recovery lock appeared after preview")

    payload = (
        _canonical_bytes(
            {
                "version": FORMAT_VERSION,
                "pid": os.getpid(),
                "host": platform.node() or "unknown-local-host",
                "created_at": _now(),
                "nonce": uuid.uuid4().hex,
            }
        )
        + b"\n"
    )
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(str(path), flags, PRIVATE_FILE_MODE)
    except FileExistsError as exc:
        raise HistoryError(
            "Another portable history recovery is already running"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(history)
        yield
    finally:
        try:
            if _read_internal(history, path) == payload:
                path.unlink()
                _fsync_directory(history)
        except (OSError, HistoryError):
            pass


def _ensure_recovery_main_lock(
    history: Path,
    expected_stale: Optional[Mapping[str, Any]],
) -> bytes:
    path = history / "LOCK"
    if expected_stale is not None:
        current = _load_stale_lock(
            history,
            path,
            label="Portable history lock",
        )
        if current is None or current["bytes"] != expected_stale["bytes"]:
            raise HistoryError("Portable history lock changed after preview")
        return current["bytes"]
    if path.exists() or path.is_symlink():
        raise HistoryError("Portable history lock appeared after preview")
    payload = (
        _canonical_bytes(
            {
                "version": FORMAT_VERSION,
                "pid": os.getpid(),
                "host": platform.node() or "unknown-local-host",
                "created_at": _now(),
                "nonce": uuid.uuid4().hex,
            }
        )
        + b"\n"
    )
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(str(path), flags, PRIVATE_FILE_MODE)
    except FileExistsError as exc:
        raise HistoryError("Portable history lock appeared after preview") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(history)
    return payload


def _unlink_exact_private(
    history: Path,
    path: Path,
    expected: bytes,
    *,
    label: str,
) -> None:
    _private_regular_metadata(path)
    if _read_internal(history, path) != expected:
        raise HistoryError("%s changed during recovery" % label)
    path.unlink()
    _fsync_directory(path.parent)


def _retire_recovered_transaction(history: Path, transaction: Path) -> None:
    _private_directory_metadata(transaction)
    temporary_root = history / "tmp"
    _private_directory_metadata(temporary_root)
    retired = temporary_root / (
        "recovered-%s-%s" % (transaction.name, uuid.uuid4().hex)
    )
    os.rename(str(transaction), str(retired))
    _fsync_directory(transaction.parent)
    _fsync_directory(temporary_root)
    try:
        shutil.rmtree(str(retired))
    except OSError:
        # The atomic rename already removed the transaction from the pending
        # namespace. A leftover private tombstone is non-authoritative and can
        # be removed during later housekeeping.
        pass


def recovery_apply(root: Any, token: str) -> Dict[str, Any]:
    """Apply only the exact recovery plan confirmed by ``recovery_preview``."""

    brain_root = _normalize_root(root)
    if not isinstance(token, str) or not _valid_id(token):
        raise HistoryError("Invalid history recovery confirmation token")
    initial = _recovery_plan(brain_root, inspect_recovery_lock=True)
    if not initial["public"]["needed"] or initial["token"] is None:
        raise HistoryError("Portable history does not require recovery")
    if not hmac.compare_digest(token, initial["token"]):
        raise HistoryError("History recovery confirmation token is stale or invalid")
    history = initial["history"]

    with _exclusive_recovery_lock(history, initial["recovery_lock"]):
        current = _recovery_plan(brain_root, inspect_recovery_lock=False)
        if current["main_digest"] != initial["main_digest"]:
            raise HistoryError("History recovery plan changed after preview")
        lock_bytes = _ensure_recovery_main_lock(history, current["lock"])
        action = current["public"]["action"]
        pending = current["pending"]
        marker = current["marker"]
        try:
            if action == "rollback-unpublished-undo":
                if (
                    pending is None
                    or current["current_entries"] is None
                    or current["public"]["changes"] is None
                ):
                    raise HistoryError("Undo rollback recovery plan is incomplete")
                _rollback_undo(
                    brain_root,
                    pending["path"],
                    _flatten_changes(current["public"]["changes"]),
                    current["current_entries"],
                )
            elif action == "cleanup-published-undo":
                if pending is None:
                    raise HistoryError("Published undo cleanup plan is incomplete")
            elif action not in {
                "clear-recovered-state",
                "clear-stale-lock",
            }:
                raise HistoryError("Unknown portable history recovery action")

            verified, _blobs, _entries, _chain = _inspect_unlocked(
                brain_root,
                history,
            )
            if pending is not None and not verified["clean"]:
                raise HistoryError(
                    "Recovered undo does not match the published history"
                )
            if pending is not None:
                _retire_recovered_transaction(history, pending["path"])
            if marker is not None:
                _unlink_exact_private(
                    history,
                    marker["path"],
                    marker["bytes"],
                    label="Portable history recovery marker",
                )
            _unlink_exact_private(
                history,
                history / "LOCK",
                lock_bytes,
                label="Portable history lock",
            )
        except Exception:
            marker_path = history / "RECOVERY_REQUIRED"
            if not marker_path.exists() and not marker_path.is_symlink():
                _write_recovery_marker(
                    history,
                    "Portable history recovery was interrupted or incomplete",
                )
            raise

        final, _blobs, _entries, _chain = _inspect_unlocked(brain_root, history)
        return {
            "action": action,
            "head": final["head"],
            "clean": final["clean"],
            "transaction_recovered": pending is not None,
        }


def undo_apply(
    root: Any,
    to_revision: str,
    token: str,
    reason: str,
) -> str:
    """Restore a prior manifest and record the restoration as a new revision."""

    brain_root = _normalize_root(root)
    reason = _require_text(reason, "reason")
    if not isinstance(token, str) or not _valid_id(token):
        raise HistoryError("Invalid undo confirmation token")
    history, format_payload = _open_history(brain_root)
    with _exclusive_lock(history):
        _check_pending(history, allow_lock=True)
        preview, current_entries, target_entries = _undo_preview_unlocked(
            brain_root,
            history,
            format_payload,
            to_revision,
        )
        if not hmac.compare_digest(token, preview["token"]):
            raise HistoryError("Undo confirmation token is stale or invalid")
        changes = preview["changes"]
        changed = _flatten_changes(changes)
        transaction = history / "transactions" / uuid.uuid4().hex
        quarantine = transaction / "quarantine"
        transaction.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        quarantine.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        journal = {
            "version": FORMAT_VERSION,
            "state": "prepared",
            "from_revision": preview["from_revision"],
            "to_revision": to_revision,
            "token": token,
            "changes": changes,
            "created_at": _now(),
        }
        _write_json(transaction / "journal.json", journal)
        revision_id: Optional[str] = None
        head_published = False
        try:
            for relative in sorted(changes["modified"] + changes["deleted"]):
                source = _path_for(brain_root, relative)
                data, _mode = _safe_read_regular(source)
                expected = current_entries[relative]
                if _digest(data) != expected["blob"] or len(data) != expected["size"]:
                    raise HistoryError(
                        "Working tree changed during undo: %s" % relative
                    )
                destination = quarantine.joinpath(*relative.split("/"))
                _ensure_private_directories(quarantine, destination.parent)
                os.replace(str(source), str(destination))
                os.chmod(str(destination), PRIVATE_FILE_MODE)

            for relative in sorted(changes["added"] + changes["modified"]):
                _materialize_entry(
                    brain_root,
                    history,
                    relative,
                    target_entries[relative],
                )

            restored, _restored_blobs = _scan_tree(brain_root)
            if restored != dict(sorted(target_entries.items())):
                raise HistoryError("Undo result does not match target manifest")

            target_revision = _load_revision(history, to_revision)
            revision_id = _store_json_object(
                history,
                "revisions",
                _revision_payload(
                    parent=preview["from_revision"],
                    manifest_id=target_revision["manifest"],
                    kind="undo",
                    summary="Undo to %s" % to_revision[:12],
                    reason=reason,
                    changed_paths=changed,
                    undo_target=to_revision,
                ),
            )
            try:
                _write_head(history, revision_id)
                head_published = True
            except Exception:
                try:
                    head_published = _read_head(history) == revision_id
                except Exception:
                    head_published = False
                raise
        except Exception as exc:
            if not head_published:
                try:
                    _rollback_undo(
                        brain_root,
                        transaction,
                        changed,
                        current_entries,
                    )
                    shutil.rmtree(str(transaction))
                except Exception as rollback_exc:
                    _write_recovery_marker(
                        history,
                        "Undo failed and rollback was incomplete: %s" % rollback_exc,
                    )
                    raise HistoryError(
                        "Undo failed and requires manual recovery"
                    ) from exc
            else:
                _write_recovery_marker(
                    history,
                    "Undo revision was published but transaction cleanup failed",
                )
            if isinstance(exc, HistoryError):
                raise
            raise HistoryError("Undo transaction failed: %s" % exc) from exc

        try:
            shutil.rmtree(str(transaction))
        except OSError as exc:
            _write_recovery_marker(
                history,
                "Undo committed but transaction cleanup failed",
            )
            raise HistoryError(
                "Undo committed but requires transaction cleanup"
            ) from exc
        if revision_id is None:
            raise HistoryError("Undo did not create a revision")
        return revision_id


__all__ = [
    "HistoryError",
    "initialize",
    "inspect",
    "changed_paths",
    "save",
    "list_revisions",
    "recovery_preview",
    "recovery_apply",
    "undo_preview",
    "undo_apply",
]
