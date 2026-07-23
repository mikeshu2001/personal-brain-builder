#!/usr/bin/env python3
"""Pure state, consent, scope, and readiness controls for Personal Brain.

This module deliberately performs no filesystem writes.  It is intended to be
imported by ``brainctl.py`` (or another runner) so that state transitions and
permission decisions stay deterministic and independently testable.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOP_LEVEL_STATES = (
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
)

AREA_COVERAGE = frozenset(
    {
        "content-reviewed",
        "names-only",
        "excluded-by-user",
        "unavailable",
        "blocked-sensitive",
        "needs-recheck",
    }
)
AREA_EXECUTION = frozenset({"pending", "in-progress", "closed"})

CONTOUR_SUBSTATES = frozenset(
    {
        "CONTOUR_PRECHECK",
        "CONTOUR_PRE_FINGERPRINT",
        "CONTOUR_READ",
        "CONTOUR_CARD",
        "CONTOUR_CONFLICT",
        "CONTOUR_JOURNAL",
        "CONTOUR_VALIDATE_SAVE",
        "CONTOUR_POST_VERIFY",
        "CONTOUR_CLOSE",
    }
)
SESSION_SUBSTATES = frozenset(
    {
        "SESSION_METADATA_MAP",
        "SESSION_STABILITY_FILTER",
        "SESSION_DISTILLATE_PASS",
        "SESSION_TRANSCRIPT_PASS",
        "SESSION_CLASSIFY",
        "SESSION_FAILURE_SET",
        "SESSION_VALIDATE_SAVE",
    }
)

CONSENT_DECISIONS = frozenset({"approved", "declined", "revoked"})
CONSENT_KINDS = frozenset(
    {
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
)
SCOPE_MODES = frozenset(
    {"metadata", "content", "names-only", "session", "connection", "exclude"}
)
CONNECTION_STATUSES = frozenset(
    {
        "not-detected",
        "unsupported",
        "manual-step-required",
        "pending-approval",
        "connected",
        "verified",
        "failed-closed",
    }
)
CHECK_STATUSES = frozenset({"pass", "fail", "blocked", "skipped"})
CONFLICT_STATUSES = frozenset({"open", "deferred", "resolved"})
SESSION_COVERAGE = frozenset(
    {
        "pending",
        "metadata-only",
        "distillates-reviewed",
        "transcripts-reviewed",
        "excluded-by-user",
        "unavailable",
        "blocked-sensitive",
        "needs-recheck",
    }
)

_NORMAL_FORWARD_TRANSITIONS: dict[str, frozenset[str]] = {
    "DISCOVER_OR_RESUME": frozenset({"COMPATIBILITY_CHECK"}),
    "COMPATIBILITY_CHECK": frozenset({"INTRO"}),
    "INTRO": frozenset({"PROFILE_SEED"}),
    "PROFILE_SEED": frozenset({"SAFETY_AND_PERMISSIONS"}),
    "SAFETY_AND_PERMISSIONS": frozenset({"BRAIN_LOCATION"}),
    "BRAIN_LOCATION": frozenset({"BRAIN_CREATE"}),
    "BRAIN_CREATE": frozenset({"MAP_PERMISSION"}),
    "MAP_PERMISSION": frozenset({"METADATA_MAP"}),
    "METADATA_MAP": frozenset({"SCOPE_REVIEW"}),
    "SCOPE_REVIEW": frozenset({"DEEP_AUDIT_GATE"}),
    "DEEP_AUDIT_GATE": frozenset({"CONTOUR_LOOP"}),
    "CONTOUR_LOOP": frozenset({"SESSIONS_PERMISSION"}),
    "SESSIONS_PERMISSION": frozenset(
        {"SESSION_HISTORY_AUDIT", "CONSOLIDATION"}
    ),
    "SESSION_HISTORY_AUDIT": frozenset({"CONSOLIDATION"}),
    "CONSOLIDATION": frozenset({"CONFLICT_REVIEW"}),
    "CONFLICT_REVIEW": frozenset({"CORE_REVIEW"}),
    "CORE_REVIEW": frozenset({"LIBRARY_VALIDATION"}),
    "LIBRARY_VALIDATION": frozenset({"CONNECTION_REVIEW"}),
    "CONNECTION_REVIEW": frozenset(
        {"CONNECTION_APPLY", "ACCEPTANCE_TESTING"}
    ),
    "CONNECTION_APPLY": frozenset({"ACCEPTANCE_TESTING"}),
    "ACCEPTANCE_TESTING": frozenset({"RECOVERY_COPY_REVIEW"}),
    "RECOVERY_COPY_REVIEW": frozenset({"HANDOFF"}),
    "HANDOFF": frozenset({"READY"}),
    "READY": frozenset({"READY"}),
    "PAUSED": frozenset(),
    "FAILED_CLOSED": frozenset({"DISCOVER_OR_RESUME"}),
}

_RESUMABLE_STATES = frozenset(
    state for state in TOP_LEVEL_STATES if state not in {"PAUSED", "FAILED_CLOSED"}
)


def _build_allowed_transitions() -> dict[str, frozenset[str]]:
    """Build the public static graph; contextual constraints are checked later."""

    graph: dict[str, frozenset[str]] = {}
    for state in TOP_LEVEL_STATES:
        targets = set(_NORMAL_FORWARD_TRANSITIONS[state])
        if state == "DISCOVER_OR_RESUME":
            # These are only legal when the exact target is recorded in
            # ``resume_from``.  transition_state enforces that condition.
            targets.update(_RESUMABLE_STATES - {"DISCOVER_OR_RESUME"})
        elif state == "PAUSED":
            # Likewise, only the exact recorded ``resume_from`` is legal.
            targets.update(_RESUMABLE_STATES)

        if state not in {"PAUSED", "FAILED_CLOSED"}:
            targets.add("PAUSED")
        if state != "FAILED_CLOSED":
            targets.add("FAILED_CLOSED")
        graph[state] = frozenset(targets)
    return graph


ALLOWED_TRANSITIONS = _build_allowed_transitions()

_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "brain_root",
        "state",
        "substate",
        "created_at",
        "entered_at",
        "updated_at",
        "next_action",
        "blocking_issue",
        "compatibility",
        "manual_steps",
        "onboarding",
        "consents",
        "scope",
        "areas",
        "session_history",
        "connections",
        "checks",
        "open_conflicts",
        "expected_revision",
        "authorized_write_set",
    }
)

_ACCESS_OPERATIONS: dict[str, tuple[frozenset[str], str]] = {
    "metadata": (frozenset({"metadata", "content", "names-only"}), "metadata-map"),
    "metadata-map": (
        frozenset({"metadata", "content", "names-only"}),
        "metadata-map",
    ),
    "content": (frozenset({"content"}), "content-audit"),
    "content-audit": (frozenset({"content"}), "content-audit"),
    "session": (frozenset({"session"}), "session-history"),
    "session-history": (frozenset({"session"}), "session-history"),
    "connection": (frozenset({"connection"}), "connection"),
}


class StateValidationError(ValueError):
    """Raised when durable state violates one or more schema invariants."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        message = "Invalid durable state"
        if self.errors:
            message += ":\n- " + "\n- ".join(self.errors)
        super().__init__(message)


class StateTransitionError(ValueError):
    """Raised when a requested state transition is not legal."""


@dataclass(frozen=True)
class AccessDecision:
    """Result of a read/access authorization decision."""

    allowed: bool
    operation: str
    requested_path: str
    matched_scope: str | None
    matched_mode: str | None
    consent_decision: str | None
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_path(raw: str | os.PathLike[str]) -> str:
    """Return one comparable absolute spelling without requiring path existence."""

    path = Path(raw).expanduser().resolve(strict=False)
    return os.path.normcase(os.path.normpath(str(path)))


def normalize_scopes(scopes: Iterable[str | os.PathLike[str]]) -> tuple[str, ...]:
    """Normalize a consent scope as an order-independent exact set of paths."""

    if isinstance(scopes, (str, bytes, os.PathLike)):
        raise TypeError("scopes must be an iterable of paths, not one path")
    normalized: set[str] = set()
    for raw in scopes:
        if not isinstance(raw, (str, os.PathLike)):
            raise TypeError("each consent scope must be a path string")
        text = os.fspath(raw)
        if not text.strip():
            raise ValueError("consent scope paths must not be empty")
        normalized.add(normalize_path(text))
    if not normalized:
        raise ValueError("consent scope must contain at least one path")
    return tuple(sorted(normalized))


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _timestamp_value(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _is_absolute_path_string(value: Any) -> bool:
    return _is_nonempty_string(value) and Path(value).expanduser().is_absolute()


def _path_is_within(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath((path, parent)) == parent
    except ValueError:
        return False


def _validate_string_list(
    errors: list[str],
    value: Any,
    field: str,
    *,
    nonempty_items: bool = True,
) -> None:
    if not _is_sequence(value):
        errors.append(f"{field} must be a list")
        return
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or (nonempty_items and not item.strip()):
            errors.append(f"{field}[{index}] must be a non-empty string")
            continue
        if item in seen:
            errors.append(f"{field}[{index}] duplicates {item!r}")
        seen.add(item)


def state_validation_errors(
    state: Mapping[str, Any] | Any,
    brain_root: str | os.PathLike[str],
) -> list[str]:
    """Return all durable-state schema errors without mutating ``state``."""

    errors: list[str] = []
    if not isinstance(state, Mapping):
        return ["state must be a mapping"]

    missing = sorted(_REQUIRED_FIELDS - state.keys())
    errors.extend(f"missing required field: {field}" for field in missing)

    schema_version = state.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 1
    ):
        errors.append("schema_version must be a positive integer")

    installation_id = state.get("installation_id")
    legacy_brain_id = state.get("brain_id")
    if not _is_nonempty_string(installation_id) and not _is_nonempty_string(
        legacy_brain_id
    ):
        errors.append("installation_id (or legacy brain_id) must be a non-empty string")
    if (
        _is_nonempty_string(installation_id)
        and _is_nonempty_string(legacy_brain_id)
        and installation_id != legacy_brain_id
    ):
        errors.append("installation_id and brain_id must identify the same installation")

    owner = state.get("owner_display_name", state.get("owner"))
    if not _is_nonempty_string(owner):
        errors.append("owner_display_name (or legacy owner) must be a non-empty string")
    language = state.get("user_language", state.get("locale"))
    if not _is_nonempty_string(language):
        errors.append("user_language (or legacy locale) must be a non-empty string")

    recorded_root = state.get("brain_root")
    if not _is_absolute_path_string(recorded_root):
        errors.append("brain_root must be a non-empty absolute path string")
    else:
        try:
            opened_root = normalize_path(brain_root)
        except (TypeError, ValueError, OSError):
            errors.append("brain_root argument must be a valid path")
        else:
            if normalize_path(recorded_root) != opened_root:
                errors.append(
                    "brain_root does not match the root used to open this state"
                )

    current_state = state.get("state")
    if current_state not in TOP_LEVEL_STATES:
        errors.append(f"state must be one of {TOP_LEVEL_STATES!r}")

    substate = state.get("substate")
    if current_state == "CONTOUR_LOOP":
        if substate is not None and substate not in CONTOUR_SUBSTATES:
            errors.append("substate is not valid for CONTOUR_LOOP")
    elif current_state == "SESSION_HISTORY_AUDIT":
        if substate is not None and substate not in SESSION_SUBSTATES:
            errors.append("substate is not valid for SESSION_HISTORY_AUDIT")
    elif substate is not None:
        errors.append("substate must be null outside contour and session audit states")

    for field in ("created_at", "entered_at", "updated_at"):
        if _timestamp_value(state.get(field)) is None:
            errors.append(f"{field} must be an ISO-8601 timestamp")

    next_action = state.get("next_action")
    blocking_issue = state.get("blocking_issue")
    if next_action is not None and not _is_nonempty_string(next_action):
        errors.append("next_action must be null or a non-empty string")
    if blocking_issue is not None and not _is_nonempty_string(blocking_issue):
        errors.append("blocking_issue must be null or a non-empty string")
    if not _is_nonempty_string(next_action) and not _is_nonempty_string(
        blocking_issue
    ):
        errors.append("state must contain either next_action or blocking_issue")

    resume_from = state.get("resume_from")
    if resume_from is not None and resume_from not in _RESUMABLE_STATES:
        errors.append("resume_from must name a resumable top-level state or be null")
    if current_state == "PAUSED" and resume_from not in _RESUMABLE_STATES:
        errors.append("PAUSED state requires an exact resume_from state")

    failed_from = state.get("failed_from")
    if failed_from is not None and failed_from not in TOP_LEVEL_STATES:
        errors.append("failed_from must name a top-level state or be null")
    if current_state == "FAILED_CLOSED":
        if failed_from not in TOP_LEVEL_STATES or failed_from == "FAILED_CLOSED":
            errors.append("FAILED_CLOSED requires a valid failed_from state")
        if not _is_nonempty_string(blocking_issue):
            errors.append("FAILED_CLOSED requires a blocking_issue")
    elif _is_nonempty_string(blocking_issue):
        errors.append("blocking_issue is only valid in FAILED_CLOSED")

    recovery_pending = state.get("recovery_pending")
    if recovery_pending is not None and not isinstance(recovery_pending, bool):
        errors.append("recovery_pending must be boolean when present")
    if recovery_pending is True and current_state != "DISCOVER_OR_RESUME":
        errors.append("recovery_pending is only valid in DISCOVER_OR_RESUME")

    compatibility = state.get("compatibility")
    if not isinstance(compatibility, Mapping):
        errors.append("compatibility must be a mapping")
    manual_steps = state.get("manual_steps")
    _validate_string_list(errors, manual_steps, "manual_steps")

    onboarding = state.get("onboarding")
    effective_state = current_state
    if current_state == "PAUSED":
        effective_state = state.get("resume_from")
    elif current_state == "FAILED_CLOSED":
        effective_state = state.get("failed_from")
    onboarding_may_be_pending = effective_state in {
        "DISCOVER_OR_RESUME",
        "COMPATIBILITY_CHECK",
        "INTRO",
        "PROFILE_SEED",
        "SAFETY_AND_PERMISSIONS",
        "BRAIN_LOCATION",
        "BRAIN_CREATE",
        "MAP_PERMISSION",
    }
    if onboarding is None:
        if not onboarding_may_be_pending:
            errors.append(
                "onboarding must be durably recorded before folder mapping begins"
            )
    elif not isinstance(onboarding, Mapping):
        errors.append("onboarding must be null or a mapping")
    else:
        required_onboarding = {
            "schema_version",
            "recorded_at",
            "introduction_confirmed",
            "work_summary_saved",
            "work_summary",
            "desired_outcome",
            "computer_use",
            "never_open",
            "read_not_store",
            "processing",
            "compatibility",
        }
        for field in sorted(required_onboarding - onboarding.keys()):
            errors.append(f"onboarding is missing {field}")
        if onboarding.get("schema_version") != 1:
            errors.append("onboarding.schema_version must be 1")
        if _timestamp_value(onboarding.get("recorded_at")) is None:
            errors.append("onboarding.recorded_at must be an ISO-8601 timestamp")
        if onboarding.get("introduction_confirmed") is not True:
            errors.append("onboarding.introduction_confirmed must be true")
        summary_saved = onboarding.get("work_summary_saved")
        summary = onboarding.get("work_summary")
        if not isinstance(summary_saved, bool):
            errors.append("onboarding.work_summary_saved must be boolean")
        elif summary_saved and not _is_nonempty_string(summary):
            errors.append(
                "onboarding.work_summary must be non-empty when it is saved"
            )
        elif not summary_saved and summary is not None:
            errors.append(
                "onboarding.work_summary must be null when it is withheld"
            )
        if not _is_nonempty_string(onboarding.get("desired_outcome")):
            errors.append("onboarding.desired_outcome must be a non-empty string")
        if onboarding.get("computer_use") not in {
            "private",
            "shared",
            "unknown",
            "withheld",
        }:
            errors.append("onboarding.computer_use is not recognized")
        _validate_string_list(
            errors,
            onboarding.get("never_open"),
            "onboarding.never_open",
        )
        _validate_string_list(
            errors,
            onboarding.get("read_not_store"),
            "onboarding.read_not_store",
        )
        processing = onboarding.get("processing")
        if not isinstance(processing, Mapping):
            errors.append("onboarding.processing must be a mapping")
        else:
            if processing.get("mode") not in {
                "local",
                "online-service",
                "unknown",
            }:
                errors.append("onboarding.processing.mode is not recognized")
            if not _is_nonempty_string(processing.get("note")):
                errors.append(
                    "onboarding.processing.note must be a non-empty string"
                )
            if processing.get("acknowledged") is not True:
                errors.append("onboarding.processing.acknowledged must be true")
        recorded_compatibility = onboarding.get("compatibility")
        if not isinstance(recorded_compatibility, Mapping):
            errors.append("onboarding.compatibility must be a mapping")
        else:
            if recorded_compatibility.get("classification") not in {
                "automatic",
                "manual-connection",
            }:
                errors.append(
                    "onboarding.compatibility.classification is not recognized"
                )
            _validate_string_list(
                errors,
                recorded_compatibility.get("manual_steps"),
                "onboarding.compatibility.manual_steps",
            )
            if (
                isinstance(compatibility, Mapping)
                and dict(compatibility) != dict(recorded_compatibility)
            ):
                errors.append(
                    "compatibility must match the confirmed onboarding record"
                )
            if (
                _is_sequence(manual_steps)
                and list(manual_steps)
                != list(recorded_compatibility.get("manual_steps", ()))
            ):
                errors.append(
                    "manual_steps must match the confirmed onboarding record"
                )

    consents = state.get("consents")
    if not _is_sequence(consents):
        errors.append("consents must be a list")
    else:
        for index, consent in enumerate(consents):
            prefix = f"consents[{index}]"
            if not isinstance(consent, Mapping):
                errors.append(f"{prefix} must be a mapping")
                continue
            if consent.get("kind") not in CONSENT_KINDS:
                errors.append(f"{prefix}.kind is not recognized")
            if consent.get("decision") not in CONSENT_DECISIONS:
                errors.append(f"{prefix}.decision is not recognized")
            if _timestamp_value(consent.get("at")) is None:
                errors.append(f"{prefix}.at must be an ISO-8601 timestamp")
            try:
                normalized_scope = normalize_scopes(consent.get("scope", ()))
            except (TypeError, ValueError, OSError) as exc:
                errors.append(f"{prefix}.scope is invalid: {exc}")
            else:
                raw_scope = consent.get("scope", ())
                if tuple(raw_scope) != normalized_scope:
                    errors.append(
                        f"{prefix}.scope must contain sorted canonical absolute paths"
                    )
            note = consent.get("note")
            if note is not None and not isinstance(note, str):
                errors.append(f"{prefix}.note must be a string when present")

    scope = state.get("scope")
    if not _is_sequence(scope):
        errors.append("scope must be a list")
    else:
        seen_scope_paths: set[str] = set()
        for index, item in enumerate(scope):
            prefix = f"scope[{index}]"
            if not isinstance(item, Mapping):
                errors.append(f"{prefix} must be a mapping")
                continue
            raw_path = item.get("path")
            if not _is_absolute_path_string(raw_path):
                errors.append(f"{prefix}.path must be a non-empty absolute path")
            else:
                normalized_path = normalize_path(raw_path)
                if raw_path != normalized_path:
                    errors.append(f"{prefix}.path must use its canonical absolute path")
                if normalized_path in seen_scope_paths:
                    errors.append(f"{prefix}.path duplicates another exact scope")
                seen_scope_paths.add(normalized_path)
            if item.get("mode") not in SCOPE_MODES:
                errors.append(f"{prefix}.mode is not recognized")
            if not _is_nonempty_string(item.get("label")):
                errors.append(f"{prefix}.label must be a non-empty string")
            if not isinstance(item.get("reason"), str):
                errors.append(f"{prefix}.reason must be a string")
            if _timestamp_value(item.get("approved_at")) is None:
                errors.append(f"{prefix}.approved_at must be an ISO-8601 timestamp")
            if "consent_scope" in item:
                try:
                    normalized_consent_scope = normalize_scopes(
                        item["consent_scope"]
                    )
                except (TypeError, ValueError, OSError) as exc:
                    errors.append(f"{prefix}.consent_scope is invalid: {exc}")
                else:
                    if tuple(item["consent_scope"]) != normalized_consent_scope:
                        errors.append(
                            f"{prefix}.consent_scope must contain sorted canonical absolute paths"
                        )

    areas = state.get("areas")
    if not isinstance(areas, Mapping):
        errors.append("areas must be a mapping")
    else:
        for area_id, area in areas.items():
            prefix = f"areas[{area_id!r}]"
            if not _is_nonempty_string(area_id):
                errors.append("area keys must be non-empty strings")
            if not isinstance(area, Mapping):
                errors.append(f"{prefix} must be a mapping")
                continue
            if "id" in area and area.get("id") != area_id:
                errors.append(f"{prefix}.id must match its mapping key")
            if not _is_nonempty_string(area.get("label")):
                errors.append(f"{prefix}.label must be a non-empty string")
            paths = area.get("paths")
            if not _is_sequence(paths) or not paths:
                errors.append(f"{prefix}.paths must be a non-empty list")
            else:
                seen_area_paths: set[str] = set()
                for path_index, raw_path in enumerate(paths):
                    if not _is_absolute_path_string(raw_path):
                        errors.append(
                            f"{prefix}.paths[{path_index}] must be an absolute path"
                        )
                        continue
                    normalized_path = normalize_path(raw_path)
                    if raw_path != normalized_path:
                        errors.append(
                            f"{prefix}.paths[{path_index}] must use its canonical absolute path"
                        )
                    if normalized_path in seen_area_paths:
                        errors.append(
                            f"{prefix}.paths[{path_index}] duplicates another path"
                        )
                    seen_area_paths.add(normalized_path)

            coverage = area.get("coverage")
            execution = area.get("execution")
            if coverage not in AREA_COVERAGE:
                errors.append(f"{prefix}.coverage is not a valid coverage status")
            if execution not in AREA_EXECUTION:
                errors.append(f"{prefix}.execution is not a valid execution status")
            if execution == "closed" and coverage == "needs-recheck":
                errors.append(
                    f"{prefix} cannot be closed while coverage needs recheck"
                )
            if coverage == "content-reviewed" and execution != "closed":
                errors.append(
                    f"{prefix} can be content-reviewed only when execution is closed"
                )
            if coverage == "content-reviewed" and execution == "closed":
                if not _is_nonempty_string(area.get("journal")):
                    errors.append(
                        f"{prefix} content-reviewed closure requires a journal card"
                    )
                if not _is_nonempty_string(area.get("saved_revision")):
                    errors.append(
                        f"{prefix} content-reviewed closure requires a saved revision"
                    )
                pre = area.get("pre_fingerprints")
                post = area.get("post_fingerprints")
                if not isinstance(pre, Mapping) or not _is_nonempty_string(
                    pre.get("sha256")
                ):
                    errors.append(
                        f"{prefix} content-reviewed closure requires a pre-read fingerprint"
                    )
                if (
                    not isinstance(post, Mapping)
                    or not _is_nonempty_string(post.get("sha256"))
                    or post.get("verified") is not True
                ):
                    errors.append(
                        f"{prefix} content-reviewed closure requires a verified post-read fingerprint"
                    )
                expected_roots = list(paths) if _is_sequence(paths) else []
                if (
                    isinstance(pre, Mapping)
                    and pre.get("roots") != expected_roots
                ):
                    errors.append(
                        f"{prefix} pre-read fingerprint roots must exactly match area paths"
                    )
                if (
                    isinstance(post, Mapping)
                    and post.get("roots") != expected_roots
                ):
                    errors.append(
                        f"{prefix} post-read fingerprint roots must exactly match area paths"
                    )

            cursor = area.get("cursor")
            if cursor is not None and not isinstance(cursor, str):
                errors.append(f"{prefix}.cursor must be a string or null")
            _validate_string_list(errors, area.get("cards"), f"{prefix}.cards")

            attempt = area.get("attempt")
            if attempt is not None and (
                not isinstance(attempt, int)
                or isinstance(attempt, bool)
                or attempt < 0
            ):
                errors.append(f"{prefix}.attempt must be a non-negative integer")
            for field in ("pre_fingerprints", "post_fingerprints"):
                if field in area and not isinstance(area[field], Mapping):
                    errors.append(f"{prefix}.{field} must be a mapping")
            if "conflicts" in area and not _is_sequence(area["conflicts"]):
                errors.append(f"{prefix}.conflicts must be a list")
            if "journal" in area and area["journal"] is not None and not isinstance(
                area["journal"], str
            ):
                errors.append(f"{prefix}.journal must be a string or null")
            if (
                "saved_revision" in area
                and area["saved_revision"] is not None
                and not _is_nonempty_string(area["saved_revision"])
            ):
                errors.append(
                    f"{prefix}.saved_revision must be null or a non-empty string"
                )

    session_history = state.get("session_history")
    if not isinstance(session_history, Mapping):
        errors.append("session_history must be a mapping")
    else:
        for source_id, source in session_history.items():
            prefix = f"session_history[{source_id!r}]"
            if not _is_nonempty_string(source_id):
                errors.append("session_history keys must be non-empty strings")
            if not isinstance(source, Mapping):
                errors.append(f"{prefix} must be a mapping")
                continue
            path = source.get("path")
            if path is not None:
                if not _is_absolute_path_string(path):
                    errors.append(f"{prefix}.path must be an absolute path or null")
                elif path != normalize_path(path):
                    errors.append(
                        f"{prefix}.path must use its canonical absolute path"
                    )
            if source.get("execution") not in AREA_EXECUTION:
                errors.append(f"{prefix}.execution is not recognized")
            if source.get("coverage") not in SESSION_COVERAGE:
                errors.append(f"{prefix}.coverage is not recognized")
            if (
                source.get("execution") == "closed"
                and source.get("coverage") in {"pending", "needs-recheck"}
            ):
                errors.append(
                    f"{prefix} cannot be closed with pending or needs-recheck coverage"
                )

    core_review = state.get("core_review")
    if core_review is not None:
        if not isinstance(core_review, Mapping):
            errors.append("core_review must be a mapping when present")
        else:
            if _timestamp_value(core_review.get("confirmed_at")) is None:
                errors.append(
                    "core_review.confirmed_at must be an ISO-8601 timestamp"
                )
            if not _is_nonempty_string(core_review.get("profile_sha256")):
                errors.append(
                    "core_review.profile_sha256 must be a non-empty string"
                )
            if not isinstance(core_review.get("note"), str):
                errors.append("core_review.note must be a string")

    connections = state.get("connections")
    if not isinstance(connections, Mapping):
        errors.append("connections must be a mapping")
    else:
        for connection_id, connection in connections.items():
            prefix = f"connections[{connection_id!r}]"
            if not _is_nonempty_string(connection_id):
                errors.append("connection keys must be non-empty strings")
            if not isinstance(connection, Mapping):
                errors.append(f"{prefix} must be a mapping")
                continue
            if connection.get("status") not in CONNECTION_STATUSES:
                errors.append(f"{prefix}.status is not recognized")
            if "target" in connection and not _is_nonempty_string(
                connection["target"]
            ):
                errors.append(f"{prefix}.target must be a non-empty string")
            elif "target" in connection:
                target = connection["target"]
                if target != normalize_path(target):
                    errors.append(
                        f"{prefix}.target must use its canonical absolute path"
                    )
            if "selected" in connection and not isinstance(
                connection["selected"], bool
            ):
                errors.append(f"{prefix}.selected must be boolean")
            if connection.get("status") == "verified" and not _is_nonempty_string(
                connection.get("evidence")
            ):
                errors.append(
                    f"{prefix}.evidence must describe the fresh-conversation verification"
                )
            for field in ("baseline", "test_result"):
                if (
                    field in connection
                    and connection[field] is not None
                    and not isinstance(connection[field], (str, Mapping))
                ):
                    errors.append(f"{prefix}.{field} must be a mapping, string, or null")

    checks = state.get("checks")
    if not isinstance(checks, Mapping):
        errors.append("checks must be a mapping")
    else:
        for check_id, check in checks.items():
            prefix = f"checks[{check_id!r}]"
            if not _is_nonempty_string(check_id):
                errors.append("check keys must be non-empty strings")
            if not isinstance(check, Mapping):
                errors.append(f"{prefix} must be a mapping")
                continue
            if check.get("status") not in CHECK_STATUSES:
                errors.append(f"{prefix}.status is not recognized")
            if not isinstance(check.get("evidence"), str):
                errors.append(f"{prefix}.evidence must be a string")
            if "checked_at" in check and _timestamp_value(check["checked_at"]) is None:
                errors.append(f"{prefix}.checked_at must be an ISO-8601 timestamp")

    conflicts = state.get("open_conflicts")
    if not _is_sequence(conflicts):
        errors.append("open_conflicts must be a list")
    else:
        seen_conflict_ids: set[str] = set()
        for index, conflict in enumerate(conflicts):
            prefix = f"open_conflicts[{index}]"
            if not isinstance(conflict, Mapping):
                errors.append(f"{prefix} must be a mapping")
                continue
            conflict_id = conflict.get("id")
            if not _is_nonempty_string(conflict_id):
                errors.append(f"{prefix}.id must be a non-empty string")
            elif conflict_id in seen_conflict_ids:
                errors.append(f"{prefix}.id duplicates {conflict_id!r}")
            else:
                seen_conflict_ids.add(conflict_id)
            if not isinstance(conflict.get("blocking"), bool):
                errors.append(f"{prefix}.blocking must be boolean")
            if conflict.get("status") not in CONFLICT_STATUSES:
                errors.append(f"{prefix}.status is not recognized")

    expected_revision = state.get("expected_revision")
    if expected_revision is not None and not _is_nonempty_string(expected_revision):
        errors.append("expected_revision must be null or a non-empty string")

    write_set = state.get("authorized_write_set")
    if not _is_sequence(write_set):
        errors.append("authorized_write_set must be a list")
    else:
        seen_writes: set[str] = set()
        normalized_root = (
            normalize_path(recorded_root)
            if _is_absolute_path_string(recorded_root)
            else None
        )
        for index, raw_path in enumerate(write_set):
            prefix = f"authorized_write_set[{index}]"
            if not _is_nonempty_string(raw_path):
                errors.append(f"{prefix} must be a non-empty path string")
                continue
            candidate = Path(raw_path)
            if candidate.is_absolute():
                normalized_write = normalize_path(raw_path)
                if normalized_root and not _path_is_within(
                    normalized_write, normalized_root
                ):
                    errors.append(f"{prefix} must stay inside brain_root")
            else:
                if ".." in candidate.parts:
                    errors.append(f"{prefix} must not contain parent traversal")
                normalized_write = candidate.as_posix()
            if normalized_write in seen_writes:
                errors.append(f"{prefix} duplicates another authorized path")
            seen_writes.add(normalized_write)

    return errors


def validate_state(
    state: Mapping[str, Any],
    brain_root: str | os.PathLike[str],
) -> None:
    """Validate durable state, raising ``StateValidationError`` on any defect."""

    errors = state_validation_errors(state, brain_root)
    if errors:
        raise StateValidationError(errors)


def active_consent_decision(
    state: Mapping[str, Any],
    kind: str,
    scopes: Iterable[str | os.PathLike[str]],
) -> str | None:
    """Return the latest decision for one kind and one exact normalized scope set."""

    requested = normalize_scopes(scopes)
    latest: tuple[float, int, str] | None = None
    consents = state.get("consents", ())
    if not _is_sequence(consents):
        return None
    for index, consent in enumerate(consents):
        if not isinstance(consent, Mapping) or consent.get("kind") != kind:
            continue
        decision = consent.get("decision")
        if decision not in CONSENT_DECISIONS:
            continue
        timestamp = _timestamp_value(consent.get("at"))
        if timestamp is None:
            continue
        try:
            recorded = normalize_scopes(consent.get("scope", ()))
        except (TypeError, ValueError, OSError):
            continue
        if recorded != requested:
            continue
        candidate = (timestamp, index, decision)
        if latest is None or candidate[:2] > latest[:2]:
            latest = candidate
    return latest[2] if latest is not None else None


def has_active_consent(
    state: Mapping[str, Any],
    kind: str,
    scopes: Iterable[str | os.PathLike[str]],
) -> bool:
    """Return true only when the latest exact consent is ``approved``."""

    return active_consent_decision(state, kind, scopes) == "approved"


def _active_approved_consent_scopes(
    state: Mapping[str, Any],
    kind: str,
) -> tuple[tuple[str, ...], ...]:
    """Return every currently approved exact scope for one consent kind."""

    distinct: set[tuple[str, ...]] = set()
    consents = state.get("consents", ())
    if not _is_sequence(consents):
        return ()
    for consent in consents:
        if not isinstance(consent, Mapping) or consent.get("kind") != kind:
            continue
        try:
            distinct.add(normalize_scopes(consent.get("scope", ())))
        except (TypeError, ValueError, OSError):
            continue
    return tuple(
        sorted(
            scope
            for scope in distinct
            if active_consent_decision(state, kind, scope) == "approved"
        )
    )


def access_decision(
    state: Mapping[str, Any],
    path: str | os.PathLike[str],
    operation: str,
) -> AccessDecision:
    """Evaluate the most-specific scope and its required active consent.

    A more-specific record is a hard boundary.  It does not inherit a broader
    parent's mode, and an ``exclude`` record wins ties at the same path.
    """

    normalized_operation = operation.strip().casefold()
    policy = _ACCESS_OPERATIONS.get(normalized_operation)
    requested_path = normalize_path(path)
    if policy is None:
        return AccessDecision(
            False,
            normalized_operation,
            requested_path,
            None,
            None,
            None,
            "unknown operation",
        )
    allowed_modes, consent_kind = policy

    matches: list[tuple[int, bool, int, Mapping[str, Any], str]] = []
    scope = state.get("scope", ())
    if _is_sequence(scope):
        for index, item in enumerate(scope):
            if not isinstance(item, Mapping):
                continue
            raw_root = item.get("path")
            if not _is_absolute_path_string(raw_root):
                continue
            normalized_root = normalize_path(raw_root)
            if not _path_is_within(requested_path, normalized_root):
                continue
            depth = len(Path(normalized_root).parts)
            is_exclude = item.get("mode") == "exclude"
            matches.append((depth, is_exclude, index, item, normalized_root))

    if not matches:
        return AccessDecision(
            False,
            normalized_operation,
            requested_path,
            None,
            None,
            None,
            "no approved scope contains the requested path",
        )

    _, _, _, selected, selected_root = max(
        matches, key=lambda match: (match[0], match[1], match[2])
    )
    selected_mode = selected.get("mode")
    if selected_mode == "exclude":
        return AccessDecision(
            False,
            normalized_operation,
            requested_path,
            selected_root,
            "exclude",
            None,
            "the most-specific scope excludes this path",
        )
    if selected_mode not in allowed_modes:
        return AccessDecision(
            False,
            normalized_operation,
            requested_path,
            selected_root,
            selected_mode if isinstance(selected_mode, str) else None,
            None,
            "the most-specific scope does not permit this operation",
        )

    consent_scope = selected.get("consent_scope", [selected_root])
    try:
        decision = active_consent_decision(state, consent_kind, consent_scope)
    except (TypeError, ValueError, OSError):
        decision = None
    if decision != "approved":
        reason = (
            f"the exact {consent_kind} consent was {decision}"
            if decision in {"declined", "revoked"}
            else f"no active exact {consent_kind} consent"
        )
        return AccessDecision(
            False,
            normalized_operation,
            requested_path,
            selected_root,
            selected_mode,
            decision,
            reason,
        )

    return AccessDecision(
        True,
        normalized_operation,
        requested_path,
        selected_root,
        selected_mode,
        decision,
        "permitted by the most-specific scope and active exact consent",
    )


def ready_gate_errors(
    state: Mapping[str, Any],
    required_check_ids: Iterable[str],
) -> list[str]:
    """Return every condition that prevents the installation becoming READY."""

    recorded_root = state.get("brain_root") if isinstance(state, Mapping) else None
    if not _is_nonempty_string(recorded_root):
        validation_errors = ["brain_root is missing or invalid"]
    else:
        validation_errors = state_validation_errors(state, recorded_root)
    errors = [f"invalid state: {error}" for error in validation_errors]

    if isinstance(required_check_ids, (str, bytes)):
        errors.append("required_check_ids must be an iterable of check IDs")
        required_ids: tuple[str, ...] = ()
    else:
        try:
            required_ids = tuple(required_check_ids)
        except TypeError:
            errors.append("required_check_ids must be iterable")
            required_ids = ()

    if any(not _is_nonempty_string(check_id) for check_id in required_ids):
        errors.append("required_check_ids must contain only non-empty strings")
    required_ids = tuple(
        dict.fromkeys(
            check_id for check_id in required_ids if _is_nonempty_string(check_id)
        )
    )

    areas = state.get("areas", {}) if isinstance(state, Mapping) else {}
    if isinstance(areas, Mapping):
        for area_id, area in areas.items():
            if not isinstance(area, Mapping):
                continue
            if area.get("execution") != "closed":
                errors.append(f"area {area_id!r} is not closed")
            if area.get("coverage") == "needs-recheck":
                errors.append(f"area {area_id!r} still needs recheck")

    checks = state.get("checks", {}) if isinstance(state, Mapping) else {}
    checks = checks if isinstance(checks, Mapping) else {}
    for check_id in required_ids:
        if check_id not in checks:
            errors.append(f"required check {check_id!r} is missing")
    for check_id, check in checks.items():
        status = check.get("status") if isinstance(check, Mapping) else None
        if status != "pass":
            errors.append(f"check {check_id!r} is {status or 'invalid'}")

    connections = state.get("connections", {}) if isinstance(state, Mapping) else {}
    if isinstance(connections, Mapping):
        for connection_id, connection in connections.items():
            if not isinstance(connection, Mapping):
                continue
            if connection.get("selected", True) and connection.get("status") != "verified":
                errors.append(f"connection {connection_id!r} is not verified")

    conflicts = (
        state.get("open_conflicts", ()) if isinstance(state, Mapping) else ()
    )
    if _is_sequence(conflicts):
        for conflict in conflicts:
            if not isinstance(conflict, Mapping):
                continue
            if conflict.get("blocking") is True and conflict.get("status") != "resolved":
                errors.append(
                    f"blocking conflict {conflict.get('id', '<unknown>')!r} is open"
                )

    if isinstance(state, Mapping):
        if not _is_nonempty_string(state.get("expected_revision")):
            errors.append("expected_revision is not recorded")
        write_set = state.get("authorized_write_set")
        if _is_sequence(write_set) and len(write_set) > 0:
            errors.append("authorized_write_set is not empty")

    return list(dict.fromkeys(errors))


def _transition_problem(
    state: Mapping[str, Any],
    target: str,
    *,
    recovery: bool,
    blocking_issue: str | None,
    required_check_ids: Iterable[str],
) -> str | None:
    source = state.get("state")
    if target not in TOP_LEVEL_STATES:
        return f"unknown target state: {target!r}"
    if source not in TOP_LEVEL_STATES:
        return f"unknown source state: {source!r}"
    if target not in ALLOWED_TRANSITIONS[source]:
        return f"transition {source} -> {target} is not allowed"

    if source == "PAUSED":
        resume_from = state.get("resume_from")
        if target == "FAILED_CLOSED":
            return None
        if target != resume_from:
            return (
                f"PAUSED may resume only its recorded state {resume_from!r}, "
                f"not {target!r}"
            )

    if source == "FAILED_CLOSED":
        if target != "DISCOVER_OR_RESUME":
            return "FAILED_CLOSED may leave only through DISCOVER_OR_RESUME"
        if not recovery:
            return "leaving FAILED_CLOSED requires explicit recovery=True"

    if source == "DISCOVER_OR_RESUME":
        resume_from = state.get("resume_from")
        if resume_from is None and target != "COMPATIBILITY_CHECK":
            return (
                "DISCOVER_OR_RESUME has no recorded resume target and may "
                "continue only to COMPATIBILITY_CHECK"
            )
        if resume_from is not None and target != resume_from:
            return (
                "DISCOVER_OR_RESUME may continue only to its exact recorded "
                f"resume_from state {resume_from!r}"
            )
        if (
            resume_from is not None
            and state.get("recovery_pending") is True
            and not recovery
        ):
            return "resuming a failed-closed state requires explicit recovery=True"

    if target == "FAILED_CLOSED" and not _is_nonempty_string(blocking_issue):
        return "entering FAILED_CLOSED requires a non-empty blocking_issue"

    prerequisite_errors: list[str] = []
    scope = state.get("scope", ())
    scope_items = (
        [item for item in scope if isinstance(item, Mapping)]
        if _is_sequence(scope)
        else []
    )
    if source == "MAP_PERMISSION" and target == "METADATA_MAP":
        if not isinstance(state.get("onboarding"), Mapping):
            prerequisite_errors.append(
                "confirmed onboarding choices have not been durably recorded"
            )
        mapped = [
            item
            for item in scope_items
            if item.get("mode") in {"metadata", "names-only", "content"}
        ]
        if not mapped:
            prerequisite_errors.append(
                "at least one exact folder must be approved for the name-only map"
            )
        for item in mapped:
            consent_scope = item.get("consent_scope") or [item.get("path")]
            if not has_active_consent(state, "metadata-map", consent_scope):
                prerequisite_errors.append(
                    f"folder {item.get('path')!r} lacks active exact metadata-map consent"
                )

    if source == "DEEP_AUDIT_GATE" and target == "CONTOUR_LOOP":
        for item in scope_items:
            if item.get("mode") != "content":
                continue
            consent_scope = item.get("consent_scope") or [item.get("path")]
            if not has_active_consent(state, "content-audit", consent_scope):
                prerequisite_errors.append(
                    f"folder {item.get('path')!r} lacks active exact content-audit consent"
                )

    if source == "CONTOUR_LOOP" and target == "SESSIONS_PERMISSION":
        areas = state.get("areas", {})
        area_paths: set[str] = set()
        if isinstance(areas, Mapping):
            for area_id, area in areas.items():
                if not isinstance(area, Mapping):
                    continue
                raw_paths = area.get("paths", ())
                if _is_sequence(raw_paths):
                    for raw_path in raw_paths:
                        if isinstance(raw_path, (str, os.PathLike)):
                            area_paths.add(normalize_path(raw_path))
                if area.get("execution") != "closed":
                    prerequisite_errors.append(f"area {area_id!r} is not closed")
                if area.get("coverage") == "needs-recheck":
                    prerequisite_errors.append(
                        f"area {area_id!r} still needs recheck"
                    )
        for item in scope_items:
            if item.get("mode") not in {"metadata", "names-only", "content"}:
                continue
            raw_path = item.get("path")
            if (
                not isinstance(raw_path, (str, os.PathLike))
                or normalize_path(raw_path) not in area_paths
            ):
                prerequisite_errors.append(
                    f"approved folder {raw_path!r} lacks an area coverage record"
                )

    if source == "SESSIONS_PERMISSION" and target == "SESSION_HISTORY_AUDIT":
        session_sources = state.get("session_history", {})
        source_paths: set[str] = set()
        if isinstance(session_sources, Mapping):
            for item in session_sources.values():
                if not isinstance(item, Mapping) or not item.get("path"):
                    continue
                source_paths.add(normalize_path(item["path"]))
        content_scopes = _active_approved_consent_scopes(
            state,
            "session-history",
        )
        if not content_scopes:
            prerequisite_errors.append(
                "no session-history source has active exact consent"
            )
        for consent_scope in content_scopes:
            for approved_path in consent_scope:
                if approved_path not in source_paths:
                    prerequisite_errors.append(
                        f"approved session-history path {approved_path!r} lacks a source record"
                    )
                if not has_active_consent(
                    state,
                    "session-history-metadata",
                    [approved_path],
                ):
                    prerequisite_errors.append(
                        f"session-history path {approved_path!r} lacks prior exact metadata consent"
                    )

    if source == "SESSIONS_PERMISSION" and target == "CONSOLIDATION":
        content_scopes = _active_approved_consent_scopes(
            state,
            "session-history",
        )
        if content_scopes:
            prerequisite_errors.append(
                "approved session-history content must be audited before consolidation"
            )
        session_sources = state.get("session_history", {})
        if isinstance(session_sources, Mapping):
            for source_id, item in session_sources.items():
                if (
                    isinstance(item, Mapping)
                    and item.get("execution") != "closed"
                ):
                    prerequisite_errors.append(
                        f"session source {source_id!r} is not closed"
                    )

    if source == "SESSION_HISTORY_AUDIT" and target == "CONSOLIDATION":
        session_sources = state.get("session_history", {})
        source_by_path: dict[str, Mapping[str, Any]] = {}
        if isinstance(session_sources, Mapping):
            for source_id, item in session_sources.items():
                if not isinstance(item, Mapping):
                    continue
                if item.get("path"):
                    source_by_path[normalize_path(item["path"])] = item
                if item.get("execution") != "closed":
                    prerequisite_errors.append(
                        f"session source {source_id!r} is not closed"
                    )
                if item.get("coverage") == "needs-recheck":
                    prerequisite_errors.append(
                        f"session source {source_id!r} still needs recheck"
                    )
        for consent_scope in _active_approved_consent_scopes(
            state,
            "session-history",
        ):
            for approved_path in consent_scope:
                item = source_by_path.get(approved_path)
                if item is None:
                    prerequisite_errors.append(
                        f"approved session-history path {approved_path!r} lacks a source record"
                    )
                    continue
                if item.get("coverage") not in {
                    "distillates-reviewed",
                    "transcripts-reviewed",
                    "excluded-by-user",
                    "unavailable",
                    "blocked-sensitive",
                }:
                    prerequisite_errors.append(
                        f"approved session-history path {approved_path!r} lacks terminal content coverage"
                    )

    if source == "CORE_REVIEW" and target == "LIBRARY_VALIDATION":
        review = state.get("core_review")
        if not isinstance(review, Mapping):
            prerequisite_errors.append(
                "the compact owner profile has not been explicitly reviewed"
            )
        else:
            if _timestamp_value(review.get("confirmed_at")) is None:
                prerequisite_errors.append(
                    "the compact owner profile lacks a valid confirmation time"
                )
            if not _is_nonempty_string(review.get("profile_sha256")):
                prerequisite_errors.append(
                    "the compact owner profile lacks a confirmed content fingerprint"
                )

    if source == "CONNECTION_REVIEW" and target == "CONNECTION_APPLY":
        connections = state.get("connections", {})
        selected = []
        if isinstance(connections, Mapping):
            selected = [
                item
                for item in connections.values()
                if isinstance(item, Mapping) and item.get("selected", True)
            ]
        if not selected:
            prerequisite_errors.append("no connection target was selected")
        for item in selected:
            target_path = item.get("target")
            if not _is_nonempty_string(target_path) or not has_active_consent(
                state,
                "connection",
                [target_path],
            ):
                prerequisite_errors.append(
                    f"connection target {target_path!r} lacks active exact consent"
                )

    if source == "CONNECTION_REVIEW" and target == "ACCEPTANCE_TESTING":
        connections = state.get("connections", {})
        if isinstance(connections, Mapping):
            for connection_id, connection in connections.items():
                if (
                    isinstance(connection, Mapping)
                    and connection.get("selected", True)
                    and connection.get("status") != "verified"
                ):
                    prerequisite_errors.append(
                        f"selected connection {connection_id!r} must be applied and verified or explicitly deselected"
                    )

    if source == "ACCEPTANCE_TESTING" and target == "RECOVERY_COPY_REVIEW":
        checks = state.get("checks", {})
        checks = checks if isinstance(checks, Mapping) else {}
        for check_id in required_check_ids:
            check = checks.get(check_id)
            status = check.get("status") if isinstance(check, Mapping) else None
            if status != "pass":
                prerequisite_errors.append(
                    f"required check {check_id!r} is {status or 'missing'}"
                )
        connections = state.get("connections", {})
        if isinstance(connections, Mapping):
            for connection_id, connection in connections.items():
                if (
                    isinstance(connection, Mapping)
                    and connection.get("selected", True)
                    and connection.get("status") != "verified"
                ):
                    prerequisite_errors.append(
                        f"connection {connection_id!r} is not verified"
                    )

    if source == "RECOVERY_COPY_REVIEW" and target == "HANDOFF":
        recovery_scopes: set[tuple[str, ...]] = set()
        consents = state.get("consents", ())
        if _is_sequence(consents):
            for consent in consents:
                if (
                    isinstance(consent, Mapping)
                    and consent.get("kind") == "recovery-copy"
                ):
                    try:
                        recovery_scopes.add(
                            normalize_scopes(consent.get("scope", ()))
                        )
                    except (TypeError, ValueError, OSError):
                        continue
        decisions = [
            active_consent_decision(state, "recovery-copy", scope)
            for scope in recovery_scopes
        ]
        decisions = [
            decision
            for decision in decisions
            if decision in {"approved", "declined", "revoked"}
        ]
        if not decisions:
            prerequisite_errors.append(
                "the owner has not recorded a backup decision"
            )
        elif "approved" in decisions:
            recovery_check = state.get("checks", {}).get("recovery-restore")
            recovery_status = (
                recovery_check.get("status")
                if isinstance(recovery_check, Mapping)
                else None
            )
            if recovery_status != "pass":
                prerequisite_errors.append(
                    "approved recovery copy lacks a passed restore check"
                )

    if prerequisite_errors:
        return "transition prerequisites failed:\n- " + "\n- ".join(
            dict.fromkeys(prerequisite_errors)
        )
    return None


def transition_state(
    state: Mapping[str, Any],
    target: str,
    brain_root: str | os.PathLike[str],
    *,
    recovery: bool = False,
    blocking_issue: str | None = None,
    next_action: str | None = None,
    required_check_ids: Iterable[str] = (),
    now: str | None = None,
) -> dict[str, Any]:
    """Return a validated copy advanced through one legal top-level transition."""

    validate_state(state, brain_root)
    required_check_ids = tuple(required_check_ids)
    problem = _transition_problem(
        state,
        target,
        recovery=recovery,
        blocking_issue=blocking_issue,
        required_check_ids=required_check_ids,
    )
    if problem:
        raise StateTransitionError(problem)

    timestamp = now or utc_now()
    if _timestamp_value(timestamp) is None:
        raise ValueError("now must be an ISO-8601 timestamp")

    result = copy.deepcopy(dict(state))
    source = result["state"]
    previous_blocking_issue = result.get("blocking_issue")
    result["state"] = target
    result["substate"] = None
    result["entered_at"] = timestamp
    result["updated_at"] = timestamp
    result["authorized_write_set"] = []
    if next_action is not None:
        if not _is_nonempty_string(next_action):
            raise ValueError("next_action must be a non-empty string when supplied")
        result["next_action"] = next_action

    if target == "PAUSED":
        result["resume_from"] = source
        result["blocking_issue"] = None
        result.pop("failed_from", None)
        result.pop("recovery_pending", None)
    elif target == "FAILED_CLOSED":
        failed_from = result.get("resume_from") if source == "PAUSED" else source
        result["failed_from"] = failed_from
        result["blocking_issue"] = blocking_issue
        result["next_action"] = None
        result.pop("recovery_pending", None)
    elif source == "FAILED_CLOSED" and target == "DISCOVER_OR_RESUME":
        failed_from = result.get("failed_from")
        result["blocking_issue"] = None
        if not _is_nonempty_string(result.get("next_action")):
            result["next_action"] = "Verify the repair and resume the recorded state."
        if _is_nonempty_string(previous_blocking_issue):
            result["last_blocking_issue"] = previous_blocking_issue
        if failed_from == "DISCOVER_OR_RESUME":
            result.pop("resume_from", None)
            result["recovery_pending"] = False
        else:
            result["resume_from"] = failed_from
            result["recovery_pending"] = True
    elif source == "PAUSED":
        result.pop("resume_from", None)
        result["blocking_issue"] = None
    elif source == "DISCOVER_OR_RESUME" and target != "COMPATIBILITY_CHECK":
        result.pop("resume_from", None)
        result.pop("failed_from", None)
        result.pop("recovery_pending", None)
        result["blocking_issue"] = None
    else:
        result["blocking_issue"] = None

    if target == "READY":
        gate_errors = ready_gate_errors(result, required_check_ids)
        if gate_errors:
            raise StateTransitionError(
                "READY gate failed:\n- " + "\n- ".join(gate_errors)
            )
    else:
        validate_state(result, brain_root)
    return result


def can_transition(
    state: Mapping[str, Any],
    target: str,
    brain_root: str | os.PathLike[str],
    *,
    recovery: bool = False,
    blocking_issue: str | None = None,
    required_check_ids: Iterable[str] = (),
) -> bool:
    """Return whether ``transition_state`` would accept the requested move."""

    try:
        transition_state(
            state,
            target,
            brain_root,
            recovery=recovery,
            blocking_issue=blocking_issue,
            required_check_ids=required_check_ids,
        )
    except (StateValidationError, StateTransitionError, TypeError, ValueError, OSError):
        return False
    return True


__all__ = [
    "ALLOWED_TRANSITIONS",
    "AREA_COVERAGE",
    "AREA_EXECUTION",
    "AccessDecision",
    "CHECK_STATUSES",
    "CONNECTION_STATUSES",
    "CONSENT_DECISIONS",
    "CONSENT_KINDS",
    "CONTOUR_SUBSTATES",
    "SESSION_SUBSTATES",
    "SCOPE_MODES",
    "StateTransitionError",
    "StateValidationError",
    "TOP_LEVEL_STATES",
    "access_decision",
    "active_consent_decision",
    "can_transition",
    "has_active_consent",
    "normalize_path",
    "normalize_scopes",
    "ready_gate_errors",
    "state_validation_errors",
    "transition_state",
    "validate_state",
]
