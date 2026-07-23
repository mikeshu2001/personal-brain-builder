"""Strict validation and deterministic search for personal-brain cards.

This module intentionally implements only the small YAML-frontmatter subset
used by the bundled card templates.  It is not a general YAML parser: unknown
fields, nesting, tags, anchors, multiline scalars, comments, and ambiguous
syntax fail closed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat as statlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union


PathLike = Union[str, os.PathLike]

CANONICAL_ROOTS = (
    "profile",
    "projects",
    "rules",
    "links",
    "journal",
    "inferred",
)
NONCANONICAL_ROOTS = ("inbox", "examples")
SCAN_ROOTS = CANONICAL_ROOTS + NONCANONICAL_ROOTS

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
ORIGIN_PRIORITY = {
    "stated": 4,
    "imported": 3,
    "inferred": 2,
    "observation": 1,
}
ALLOWED_SOURCE_KINDS = {"file", "git", "sheet", "chat", "session"}
ALLOWED_RISKS = {"low", "medium", "high"}

ALLOWED_FIELDS = {
    "title",
    "type",
    "created",
    "origin",
    "summary",
    "tags",
    "project",
    "stated_in",
    "sources",
    "imported_at",
    "volatile",
    "as_of",
    "receipts",
    "risk",
    "valid_from",
    "valid_to",
    "closed_at",
    "supersedes",
    "superseded_by",
    "canonical_key",
    "example",
    "stale_after_days",
}
COLLECTION_FIELDS = {"sources", "receipts"}
STRING_FIELDS = {
    "title",
    "type",
    "created",
    "origin",
    "summary",
    "project",
    "stated_in",
    "imported_at",
    "as_of",
    "risk",
    "valid_from",
    "valid_to",
    "closed_at",
    "supersedes",
    "superseded_by",
    "canonical_key",
}
DATE_FIELDS = {
    "created",
    "imported_at",
    "as_of",
    "valid_from",
    "valid_to",
    "closed_at",
}

KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
PATH_PART_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
CARD_ID_RE = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*\Z"
)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
TOP_LEVEL_RE = re.compile(r"([a-z][a-z0-9_]*):(.*)\Z")
LIST_ITEM_RE = re.compile(r"  - ([a-z][a-z0-9_]*):(.*)\Z")
LIST_CONTINUATION_RE = re.compile(r"    ([a-z][a-z0-9_]*):(.*)\Z")
WIKILINK_RE = re.compile(r"\[\[([^\[\]\r\n]+)\]\]")
WORD_RE = re.compile(r"[\w-]+", re.UNICODE)


def _secret_value_patterns() -> List[re.Pattern]:
    prefixes = {
        "github_classic": "gh" + "p_",
        "github_fine": "github_" + "pat_",
        "openai_style": "s" + "k-",
        "slack_style": "xo" + "xb-",
        "aws_access": "AK" + "IA",
    }
    return [
        re.compile(re.escape(prefixes["github_classic"]) + r"[A-Za-z0-9]{20,}"),
        re.compile(re.escape(prefixes["github_fine"]) + r"[A-Za-z0-9_]{20,}"),
        re.compile(re.escape(prefixes["openai_style"]) + r"[A-Za-z0-9_-]{20,}"),
        re.compile(re.escape(prefixes["slack_style"]) + r"[A-Za-z0-9-]{20,}"),
        re.compile(re.escape(prefixes["aws_access"]) + r"[0-9A-Z]{16}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
        re.compile(
            r"(?:password|passwd|api[_-]?key|(?:access[_-]?)?token|secret)"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:cookie|session[_-](?:id|token)|client[_-]?secret|"
            r"private[_-]?key|connection[_-]?string|database[_-]?url|dsn|"
            r"webhook[_-]?secret|signing[_-]?key)"
            r"\s*[:=]\s*[\"']?[^\s\"']{8,}",
            re.IGNORECASE,
        ),
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{8,}\."
            r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
        ),
        re.compile(
            r"(?:seed|mnemonic|recovery[_ -]?phrase)"
            r"\s*[:=]\s*[\"']?"
            r"(?:[a-z]{3,}\s+){11,23}[a-z]{3,}",
            re.IGNORECASE,
        ),
        re.compile(r"://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
    ]


SECRET_VALUE_PATTERNS = _secret_value_patterns()
SENSITIVE_PATH_PATTERNS = [
    re.compile(
        r"(?:^|[/\\:])\.env(?:[./\\:#]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[/\\:])(?:secrets?|credentials?|private[-_]?keys?|wallets?)"
        r"(?:[/\\:#]|$)",
        re.IGNORECASE,
    ),
]
SECRET_PATTERNS = [*SECRET_VALUE_PATTERNS, *SENSITIVE_PATH_PATTERNS]


class FrontmatterError(ValueError):
    """Raised when frontmatter is outside the accepted safe subset."""


@dataclass(frozen=True)
class _Card:
    path: Path
    relative_path: str
    card_id: str
    fields: Dict[str, Any]
    body: str
    raw: str

    @property
    def path_parts(self) -> Tuple[str, ...]:
        return tuple(Path(self.relative_path).parts)

    @property
    def is_example(self) -> bool:
        return "examples" in self.path_parts or self.fields.get("example") is True

    @property
    def is_canonical(self) -> bool:
        return (
            bool(self.path_parts)
            and self.path_parts[0] in CANONICAL_ROOTS
            and not self.is_example
        )

    @property
    def is_closed(self) -> bool:
        return bool(
            self.fields.get("valid_to")
            or self.fields.get("closed_at")
            or self.fields.get("superseded_by")
        )


def _error(path: str, message: str) -> str:
    return "{}: {}".format(path, message)


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def contains_secret_text(value: str) -> bool:
    """Return whether text contains a blocked credential-shaped value."""

    return _contains_secret(value)


def contains_secret_value_text(value: str) -> bool:
    """Return whether text contains a credential-shaped value."""

    return any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS)


def _read_private_json(path: Path) -> Any:
    expected = path.lstat()
    if not statlib.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
        raise OSError("must be one regular file")
    if expected.st_mode & 0o077:
        raise OSError("permissions allow another local account")
    if hasattr(os, "getuid") and expected.st_uid != os.getuid():
        raise OSError("belongs to another user")
    if expected.st_size > 10_000_000:
        raise OSError("is unexpectedly large")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not statlib.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino)
            != (expected.st_dev, expected.st_ino)
        ):
            raise OSError("changed identity while opening")
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 10_000_000:
                raise OSError("became unexpectedly large")
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
            raise OSError("changed while reading")
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    return json.loads(raw.decode("utf-8"))


def _read_regular_utf8(path: Path, *, max_size: int = 2_000_000) -> str:
    expected = path.lstat()
    if not statlib.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
        raise OSError("must be one regular file")
    if hasattr(os, "getuid") and expected.st_uid != os.getuid():
        raise OSError("belongs to another user")
    if expected.st_size > max_size:
        raise OSError("is unexpectedly large")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not statlib.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino)
            != (expected.st_dev, expected.st_ino)
        ):
            raise OSError("changed identity while opening")
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                raise OSError("became unexpectedly large")
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
            raise OSError("changed while reading")
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(descriptor)


def _parse_single_quoted(raw: str) -> str:
    if len(raw) < 2 or raw[-1] != "'":
        raise FrontmatterError("unterminated single-quoted scalar")
    inner = raw[1:-1]
    output: List[str] = []
    index = 0
    while index < len(inner):
        if inner[index] != "'":
            output.append(inner[index])
            index += 1
            continue
        if index + 1 >= len(inner) or inner[index + 1] != "'":
            raise FrontmatterError("single quotes inside a scalar must be doubled")
        output.append("'")
        index += 2
    return "".join(output)


def _split_inline_list(raw: str) -> List[str]:
    content = raw[1:-1]
    if not content.strip():
        return []
    parts: List[str] = []
    start = 0
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(content):
        char = content[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = None
            index += 1
            continue
        if quote == "'":
            if char == "'":
                if index + 1 < len(content) and content[index + 1] == "'":
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == ",":
            parts.append(content[start:index])
            start = index + 1
        elif char in "[]{}":
            raise FrontmatterError("nested collections are not supported")
        index += 1
    if quote is not None or escaped:
        raise FrontmatterError("unterminated quote in inline list")
    parts.append(content[start:])
    if any(not part.strip() for part in parts):
        raise FrontmatterError("inline list contains an empty item")
    return parts


def _reject_control_characters(value: str) -> None:
    if any(ord(char) < 32 and char not in {"\t"} for char in value):
        raise FrontmatterError("scalar contains a control character")
    if "\t" in value or "\r" in value or "\n" in value or "\x00" in value:
        raise FrontmatterError("scalar must fit on one line")


def _parse_scalar(raw: str, *, allow_list: bool = True) -> Any:
    value = raw.strip()
    if not value:
        raise FrontmatterError("empty or null scalars are not supported")

    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise FrontmatterError("invalid double-quoted scalar") from exc
        if not isinstance(parsed, str):
            raise FrontmatterError("quoted scalar must decode to a string")
        _reject_control_characters(parsed)
        return parsed

    if value.startswith("'"):
        parsed = _parse_single_quoted(value)
        _reject_control_characters(parsed)
        return parsed

    if value.startswith("["):
        if not allow_list:
            raise FrontmatterError("nested inline lists are not supported")
        if not value.endswith("]"):
            raise FrontmatterError("unterminated inline list")
        items = [
            _parse_scalar(part, allow_list=False)
            for part in _split_inline_list(value)
        ]
        if any(isinstance(item, (list, dict)) for item in items):
            raise FrontmatterError("inline list items must be scalars")
        return items

    lowered = value.casefold()
    if lowered in {"null", "~", "yes", "no", "on", "off"}:
        raise FrontmatterError("ambiguous YAML scalar is not supported")
    if value == "true":
        return True
    if value == "false":
        return False
    if re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)

    if value[0] in "-?:,{}#&*!|>'\"%@`":
        raise FrontmatterError("plain scalar starts with a reserved character")
    if any(char in value for char in "[]{}"):
        raise FrontmatterError("collection markers require supported list syntax")
    if "#" in value:
        raise FrontmatterError("plain scalars containing '#' must be quoted")
    if re.search(r":\s", value):
        raise FrontmatterError("plain scalars containing ': ' must be quoted")
    if re.search(r"(?:^|\s)[&*!][^\s]*", value):
        raise FrontmatterError("YAML anchors, aliases, and tags are not supported")
    if "\t" in value or "\x00" in value:
        raise FrontmatterError("plain scalar contains a forbidden character")
    _reject_control_characters(value)
    return value


def _mapping_value(rest: str) -> Any:
    if not rest.startswith(" "):
        raise FrontmatterError("mapping values require one separating space")
    return _parse_scalar(rest[1:], allow_list=False)


def _parse_mapping_list(
    lines: List[str],
    start: int,
    field: str,
) -> Tuple[List[Dict[str, Any]], int]:
    items: List[Dict[str, Any]] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if not line[0].isspace():
            break
        item_match = LIST_ITEM_RE.fullmatch(line)
        if item_match is None:
            raise FrontmatterError(
                "{} must be a list of mappings with two-space indentation".format(
                    field
                )
            )
        mapping: Dict[str, Any] = {}
        key = item_match.group(1)
        if not KEY_RE.fullmatch(key):
            raise FrontmatterError("invalid mapping key")
        mapping[key] = _mapping_value(item_match.group(2))
        index += 1

        while index < len(lines):
            continuation = lines[index]
            if not continuation.strip():
                index += 1
                continue
            if not continuation[0].isspace() or LIST_ITEM_RE.fullmatch(continuation):
                break
            continuation_match = LIST_CONTINUATION_RE.fullmatch(continuation)
            if continuation_match is None:
                raise FrontmatterError(
                    "{} mapping uses unsupported indentation or nesting".format(field)
                )
            continuation_key = continuation_match.group(1)
            if continuation_key in mapping:
                raise FrontmatterError(
                    "duplicate key '{}' in {} entry".format(
                        continuation_key, field
                    )
                )
            mapping[continuation_key] = _mapping_value(
                continuation_match.group(2)
            )
            index += 1
        items.append(mapping)

    if not items:
        raise FrontmatterError("{} must contain at least one mapping".format(field))
    return items, index


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if "\x00" in text:
        raise FrontmatterError("file contains a NUL byte")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise FrontmatterError("missing exact opening '---' delimiter")
    closing_index: Optional[int] = None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            closing_index = index
            break
    if closing_index is None:
        raise FrontmatterError("missing exact closing '---' delimiter")

    frontmatter_lines = lines[1:closing_index]
    fields: Dict[str, Any] = {}
    index = 0
    while index < len(frontmatter_lines):
        line = frontmatter_lines[index]
        if not line.strip():
            index += 1
            continue
        if line[0].isspace():
            raise FrontmatterError("unexpected indentation at top level")
        match = TOP_LEVEL_RE.fullmatch(line)
        if match is None:
            raise FrontmatterError("unsupported frontmatter line")
        key, rest = match.groups()
        if key not in ALLOWED_FIELDS:
            raise FrontmatterError("unknown frontmatter field '{}'".format(key))
        if key in fields:
            raise FrontmatterError("duplicate frontmatter field '{}'".format(key))
        if rest == "":
            if key not in COLLECTION_FIELDS:
                raise FrontmatterError(
                    "empty scalar '{}' is not supported".format(key)
                )
            value, index = _parse_mapping_list(
                frontmatter_lines, index + 1, key
            )
            fields[key] = value
            continue
        if key in COLLECTION_FIELDS:
            raise FrontmatterError(
                "{} must use a block list of mappings".format(key)
            )
        if not rest.startswith(" "):
            raise FrontmatterError(
                "field '{}' requires a space after ':'".format(key)
            )
        fields[key] = _parse_scalar(rest[1:])
        index += 1

    body = "\n".join(lines[closing_index + 1 :])
    return fields, body


def _real_date(value: Any) -> Optional[dt.date]:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _discover_card_paths(
    brain_root: Path,
    *,
    roots: Tuple[str, ...] = SCAN_ROOTS,
    skip_example_paths: bool = False,
) -> Tuple[List[Path], List[str]]:
    paths: List[Path] = []
    errors: List[str] = []
    for root_name in roots:
        root = brain_root / root_name
        if not root.exists():
            continue
        if root.is_symlink():
            errors.append(
                _error(root_name, "card root must not be a symbolic link")
            )
            continue
        if not root.is_dir():
            errors.append(_error(root_name, "card root is not a directory"))
            continue
        for current_raw, directory_names, file_names in os.walk(
            str(root), topdown=True, followlinks=False
        ):
            current = Path(current_raw)
            kept_directories: List[str] = []
            for directory_name in sorted(directory_names):
                directory = current / directory_name
                if directory.is_symlink():
                    relative = directory.relative_to(brain_root).as_posix()
                    errors.append(
                        _error(relative, "symbolic-link card directories are forbidden")
                    )
                    continue
                kept_directories.append(directory_name)
            directory_names[:] = kept_directories
            for file_name in sorted(file_names):
                if not file_name.endswith(".md"):
                    continue
                path = current / file_name
                relative = path.relative_to(brain_root).as_posix()
                if skip_example_paths and "examples" in Path(relative).parts:
                    continue
                if path.is_symlink():
                    errors.append(
                        _error(relative, "symbolic-link cards are forbidden")
                    )
                    continue
                try:
                    item = path.lstat()
                except OSError as exc:
                    errors.append(_error(relative, "cannot inspect card: {}".format(exc)))
                    continue
                if not statlib.S_ISREG(item.st_mode) or item.st_nlink != 1:
                    errors.append(
                        _error(relative, "card must be one regular file")
                    )
                    continue
                paths.append(path)
    return sorted(set(paths)), errors


def _validate_path(card: _Card, errors: List[str]) -> None:
    parts = Path(card.card_id).parts
    if any(PATH_PART_RE.fullmatch(part) is None for part in parts):
        errors.append(
            _error(card.relative_path, "path must use ASCII kebab-case")
        )


def _validate_field_types(card: _Card, errors: List[str]) -> None:
    fields = card.fields
    for required in ("title", "type", "created", "origin"):
        if required not in fields:
            errors.append(
                _error(card.relative_path, "missing required field '{}'".format(required))
            )

    for field in STRING_FIELDS:
        if field not in fields:
            continue
        value = fields[field]
        if not isinstance(value, str) or not value.strip():
            errors.append(
                _error(card.relative_path, "'{}' must be a non-empty string".format(field))
            )

    if "tags" in fields:
        tags = fields["tags"]
        if (
            not isinstance(tags, list)
            or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        ):
            errors.append(
                _error(card.relative_path, "'tags' must be an inline list of strings")
            )

    for boolean_field in ("volatile", "example"):
        if boolean_field in fields and type(fields[boolean_field]) is not bool:
            errors.append(
                _error(card.relative_path, "'{}' must be true or false".format(boolean_field))
            )

    if "stale_after_days" in fields:
        days = fields["stale_after_days"]
        if type(days) is not int or days < 1 or days > 36500:
            errors.append(
                _error(
                    card.relative_path,
                    "'stale_after_days' must be an integer from 1 to 36500",
                )
            )

    if "sources" in fields and not isinstance(fields["sources"], list):
        errors.append(
            _error(card.relative_path, "'sources' must be a list of mappings")
        )
    if "receipts" in fields and not isinstance(fields["receipts"], list):
        errors.append(
            _error(card.relative_path, "'receipts' must be a list of mappings")
        )

    if fields.get("type") not in ALLOWED_TYPES:
        errors.append(
            _error(card.relative_path, "invalid card type {!r}".format(fields.get("type")))
        )
    if fields.get("origin") not in ALLOWED_ORIGINS:
        errors.append(
            _error(card.relative_path, "invalid origin {!r}".format(fields.get("origin")))
        )
    if "risk" in fields and fields["risk"] not in ALLOWED_RISKS:
        errors.append(
            _error(card.relative_path, "risk must be low, medium, or high")
        )


def _validate_dates(card: _Card, errors: List[str]) -> Dict[str, dt.date]:
    parsed: Dict[str, dt.date] = {}
    for field in DATE_FIELDS:
        if field not in card.fields:
            continue
        date_value = _real_date(card.fields[field])
        if date_value is None:
            errors.append(
                _error(
                    card.relative_path,
                    "{} must be a real calendar date in YYYY-MM-DD form".format(
                        field
                    ),
                )
            )
            continue
        parsed[field] = date_value

    if "valid_from" in parsed and "valid_to" in parsed:
        if parsed["valid_from"] > parsed["valid_to"]:
            errors.append(
                _error(card.relative_path, "valid_from must not be after valid_to")
            )
    if "created" in parsed and "closed_at" in parsed:
        if parsed["closed_at"] < parsed["created"]:
            errors.append(
                _error(card.relative_path, "closed_at must not be before created")
            )
    if "valid_to" in parsed and "closed_at" in parsed:
        if parsed["closed_at"] < parsed["valid_to"]:
            errors.append(
                _error(card.relative_path, "closed_at must not be before valid_to")
            )
    return parsed


def _validate_sources(
    card: _Card,
    errors: List[str],
    source_aliases: Dict[str, Path],
    authorized_source_aliases: Set[str],
    authorized_content_paths: Tuple[Path, ...],
    known_session_source_ids: Set[str],
    session_source_ids: Set[str],
) -> None:
    if "sources" not in card.fields:
        return
    sources = card.fields["sources"]
    if not isinstance(sources, list) or not sources:
        errors.append(
            _error(card.relative_path, "sources must be a non-empty list")
        )
        return
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict) or len(source) != 1:
            errors.append(
                _error(
                    card.relative_path,
                    "source {} must contain exactly one source kind".format(index),
                )
            )
            continue
        kind, value = next(iter(source.items()))
        if kind not in ALLOWED_SOURCE_KINDS:
            errors.append(
                _error(
                    card.relative_path,
                    "source {} uses unsupported kind {!r}".format(index, kind),
                )
            )
        if not isinstance(value, str) or not value.strip():
            errors.append(
                _error(
                    card.relative_path,
                    "source {} must have a non-empty scalar value".format(index),
                )
            )
            continue
        if _contains_secret(value):
            errors.append(
                _error(
                    card.relative_path,
                    "source {} contains a secret-like value".format(index),
                )
            )
            continue
        normalized = value.strip()
        if (
            Path(normalized).expanduser().is_absolute()
            or normalized.startswith("~")
            or re.match(r"^[A-Za-z]:[/\\]", normalized)
        ):
            errors.append(
                _error(
                    card.relative_path,
                    "source {} must use a private source alias, not an absolute path".format(
                        index
                    ),
                )
            )
            continue
        if ":" not in normalized:
            errors.append(
                _error(
                    card.relative_path,
                    "source {} must contain an alias followed by ':' and a locator".format(
                        index
                    ),
                )
            )
            continue
        source_head, locator = normalized.split(":", 1)
        source_id = source_head.casefold()
        if kind == "git":
            source_id, separator, revision = source_id.partition("@")
            if separator and not revision:
                errors.append(
                    _error(
                        card.relative_path,
                        "source {} git revision after '@' must not be empty".format(
                            index
                        ),
                    )
                )
        if not locator.strip():
            errors.append(
                _error(
                    card.relative_path,
                    "source {} locator after ':' must not be empty".format(index),
                )
            )
            continue
        locator_path = locator.split("#", 1)[0].split("?", 1)[0]
        locator_parts = tuple(
            part for part in re.split(r"[/\\]+", locator_path) if part
        )
        if (
            locator.startswith(("/", "\\", "~"))
            or re.match(r"^[A-Za-z]:[/\\]", locator)
            or ".." in locator_parts
        ):
            errors.append(
                _error(
                    card.relative_path,
                    "source {} locator must stay inside its approved source root".format(
                        index
                    ),
                )
            )
            continue
        if kind == "session":
            if source_id not in known_session_source_ids:
                errors.append(
                    _error(
                        card.relative_path,
                        "source {} uses unknown session source id {!r}".format(
                            index, source_id
                        ),
                    )
                )
            elif source_id not in session_source_ids:
                errors.append(
                    _error(
                        card.relative_path,
                        "source {} session source id {!r} lacks approved content coverage".format(
                            index, source_id
                        ),
                    )
                )
        elif source_id not in source_aliases:
            errors.append(
                _error(
                    card.relative_path,
                    "source {} uses unknown source-root alias {!r}".format(
                        index, source_id
                    ),
                )
            )
        elif source_id not in authorized_source_aliases:
            errors.append(
                _error(
                    card.relative_path,
                    "source {} source-root alias {!r} lacks approved content coverage".format(
                        index, source_id
                    ),
                )
            )
        elif kind in {"file", "git"}:
            source_root = source_aliases[source_id]
            source_relative = locator_path.replace("\\", "/")
            try:
                resolved_source = (source_root / source_relative).resolve(
                    strict=False
                )
                resolved_source.relative_to(source_root)
            except (OSError, ValueError):
                errors.append(
                    _error(
                        card.relative_path,
                        "source {} resolves outside its approved source root".format(
                            index
                        ),
                    )
                )
                continue
            if not any(
                resolved_source == allowed
                or allowed in resolved_source.parents
                for allowed in authorized_content_paths
            ):
                errors.append(
                    _error(
                        card.relative_path,
                        "source {} locator is outside every content-reviewed area".format(
                            index
                        ),
                    )
                )
        elif source_id in source_aliases:
            source_root = source_aliases[source_id]
            if not any(
                source_root == allowed
                or source_root in allowed.parents
                or allowed in source_root.parents
                for allowed in authorized_content_paths
            ):
                errors.append(
                    _error(
                        card.relative_path,
                        "source {} alias is outside every content-reviewed area".format(
                            index
                        ),
                    )
                )


def _validate_receipts(
    card: _Card,
    errors: List[str],
    known_session_source_ids: Set[str],
    session_source_ids: Set[str],
) -> None:
    receipts = card.fields.get("receipts")
    if receipts is None:
        return
    if not isinstance(receipts, list) or not receipts:
        errors.append(
            _error(card.relative_path, "receipts must be a non-empty list")
        )
        return

    sessions: Set[str] = set()
    valid_receipt_count = 0
    expected_keys = {"session", "date", "quote"}
    for index, receipt in enumerate(receipts, start=1):
        if not isinstance(receipt, dict):
            errors.append(
                _error(card.relative_path, "receipt {} must be a mapping".format(index))
            )
            continue
        keys = set(receipt)
        if keys != expected_keys:
            errors.append(
                _error(
                    card.relative_path,
                    "receipt {} must contain exactly session, date, and quote".format(
                        index
                    ),
                )
            )
            continue
        session = receipt["session"]
        quote = receipt["quote"]
        receipt_date = _real_date(receipt["date"])
        valid = True
        if not isinstance(session, str) or not session.strip():
            errors.append(
                _error(
                    card.relative_path,
                    "receipt {} session must be a non-empty string".format(index),
                )
            )
            valid = False
        else:
            normalized_session = session.strip().casefold()
            if normalized_session in sessions:
                errors.append(
                    _error(
                        card.relative_path,
                        "receipt sessions must be unique independent sessions",
                    )
                )
                valid = False
            sessions.add(normalized_session)
            source_id = normalized_session.split(":", 1)[0]
            if source_id not in known_session_source_ids:
                errors.append(
                    _error(
                        card.relative_path,
                        "receipt {} uses unknown session source id {!r}".format(
                            index, source_id
                        ),
                    )
                )
                valid = False
            elif source_id not in session_source_ids:
                errors.append(
                    _error(
                        card.relative_path,
                        "receipt {} session source id {!r} lacks approved content coverage".format(
                            index, source_id
                        ),
                    )
                )
                valid = False
        if receipt_date is None:
            errors.append(
                _error(
                    card.relative_path,
                    "receipt {} date must be a real YYYY-MM-DD date".format(index),
                )
            )
            valid = False
        if not isinstance(quote, str) or not quote:
            errors.append(
                _error(
                    card.relative_path,
                    "receipt {} quote must be a non-empty string".format(index),
                )
            )
            valid = False
        elif len(quote) > 200:
            errors.append(
                _error(
                    card.relative_path,
                    "receipt {} quote exceeds 200 characters".format(index),
                )
            )
            valid = False
        elif _contains_secret(quote):
            errors.append(
                _error(
                    card.relative_path,
                    "receipt {} quote contains a secret-like value".format(index),
                )
            )
            valid = False
        if valid:
            valid_receipt_count += 1

    if card.fields.get("origin") == "inferred":
        minimum = 3 if card.fields.get("risk") == "high" else 2
        if valid_receipt_count < minimum:
            errors.append(
                _error(
                    card.relative_path,
                    "inferred card requires at least {} independent valid receipts".format(
                        minimum
                    ),
                )
            )


def _validate_origin(card: _Card, errors: List[str]) -> None:
    fields = card.fields
    origin = fields.get("origin")
    if origin == "stated":
        if not isinstance(fields.get("stated_in"), str) or not fields.get(
            "stated_in", ""
        ).strip():
            errors.append(
                _error(card.relative_path, "stated card requires stated_in")
            )
    elif origin == "imported":
        if _real_date(fields.get("imported_at")) is None:
            errors.append(
                _error(
                    card.relative_path,
                    "imported card requires imported_at as a real YYYY-MM-DD date",
                )
            )
        if "sources" not in fields:
            errors.append(
                _error(card.relative_path, "imported card requires sources")
            )
    elif origin == "inferred" and "receipts" not in fields:
        minimum = 3 if fields.get("risk") == "high" else 2
        errors.append(
            _error(
                card.relative_path,
                "inferred card requires at least {} independent valid receipts".format(
                    minimum
                ),
            )
        )

    if fields.get("volatile") is True and _real_date(fields.get("as_of")) is None:
        errors.append(
            _error(card.relative_path, "volatile card requires a real as_of date")
        )
    if "stale_after_days" in fields and fields.get("volatile") is not True:
        errors.append(
            _error(
                card.relative_path,
                "stale_after_days is allowed only when volatile is true",
            )
        )


def _validate_closed_state(card: _Card, errors: List[str]) -> None:
    fields = card.fields
    has_valid_to = "valid_to" in fields
    has_closed_at = "closed_at" in fields
    has_successor = "superseded_by" in fields
    if has_valid_to != has_closed_at:
        errors.append(
            _error(
                card.relative_path,
                "closed cards require both valid_to and closed_at",
            )
        )
    if has_successor and not (has_valid_to and has_closed_at):
        errors.append(
            _error(
                card.relative_path,
                "superseded_by requires a consistently closed card",
            )
        )
    if "supersedes" in fields and "valid_from" not in fields:
        errors.append(
            _error(card.relative_path, "supersedes requires valid_from")
        )
    if fields.get("supersedes") == card.card_id:
        errors.append(
            _error(card.relative_path, "card cannot supersede itself")
        )
    if fields.get("superseded_by") == card.card_id:
        errors.append(
            _error(card.relative_path, "card cannot be superseded by itself")
        )


def _validate_example_state(card: _Card, errors: List[str]) -> None:
    if "examples" in card.path_parts and card.fields.get("example") is not True:
        errors.append(
            _error(
                card.relative_path,
                "cards under examples must declare example: true",
            )
        )


def _parse_wikilinks(card: _Card, errors: List[str]) -> List[str]:
    matches = list(WIKILINK_RE.finditer(card.body))
    scrubbed = WIKILINK_RE.sub("", card.body)
    if "[[" in scrubbed or "]]" in scrubbed:
        errors.append(
            _error(card.relative_path, "body contains a malformed internal link")
        )
    targets: List[str] = []
    for match in matches:
        raw_target = match.group(1).strip()
        target = raw_target.split("|", 1)[0].split("#", 1)[0].strip()
        if not target or CARD_ID_RE.fullmatch(target) is None:
            errors.append(
                _error(
                    card.relative_path,
                    "invalid internal link '[[{}]]'".format(raw_target),
                )
            )
            continue
        targets.append(target)
    return targets


def _validate_references(cards: Dict[str, _Card], errors: List[str]) -> None:
    for card_id in sorted(cards):
        card = cards[card_id]
        for field in ("project", "supersedes", "superseded_by"):
            target = card.fields.get(field)
            if target is None:
                continue
            if not isinstance(target, str) or CARD_ID_RE.fullmatch(target) is None:
                errors.append(
                    _error(
                        card.relative_path,
                        "{} must be an ASCII kebab card identifier".format(field),
                    )
                )
                continue
            if target not in cards:
                errors.append(
                    _error(
                        card.relative_path,
                        "{} points to missing card '{}'".format(field, target),
                    )
                )
        for target in _parse_wikilinks(card, errors):
            if target not in cards:
                errors.append(
                    _error(
                        card.relative_path,
                        "broken internal link '[[{}]]'".format(target),
                    )
                )


def _validate_canonical_keys(cards: Dict[str, _Card], errors: List[str]) -> None:
    active_keys: Dict[str, List[str]] = {}
    for card in cards.values():
        key = card.fields.get("canonical_key")
        if not card.is_canonical or card.is_closed or key is None:
            continue
        if not isinstance(key, str) or not key.strip():
            continue
        active_keys.setdefault(key.strip().casefold(), []).append(card.card_id)
    for key, card_ids in sorted(active_keys.items()):
        if len(card_ids) > 1:
            errors.append(
                "duplicate active canonical_key {!r}: {}".format(
                    key, ", ".join(sorted(card_ids))
                )
            )


def _validate_supersession(cards: Dict[str, _Card], errors: List[str]) -> None:
    old_to_declared_successors: Dict[str, Set[str]] = {}
    successor_to_declared_old: Dict[str, Set[str]] = {}

    for card_id in sorted(cards):
        card = cards[card_id]
        predecessor = card.fields.get("supersedes")
        successor = card.fields.get("superseded_by")
        if isinstance(predecessor, str) and predecessor in cards:
            old_to_declared_successors.setdefault(predecessor, set()).add(card_id)
            old = cards[predecessor]
            if old.fields.get("superseded_by") != card_id:
                errors.append(
                    _error(
                        card.relative_path,
                        "supersedes is not reciprocal with '{}'".format(predecessor),
                    )
                )
            old_key = old.fields.get("canonical_key")
            new_key = card.fields.get("canonical_key")
            if (
                not isinstance(old_key, str)
                or not old_key.strip()
                or not isinstance(new_key, str)
                or not new_key.strip()
            ):
                errors.append(
                    _error(
                        card.relative_path,
                        "both supersession cards require canonical_key",
                    )
                )
            elif old_key.strip().casefold() != new_key.strip().casefold():
                errors.append(
                    _error(
                        card.relative_path,
                        "supersession cards must keep the same canonical_key",
                    )
                )
        if isinstance(successor, str) and successor in cards:
            successor_to_declared_old.setdefault(successor, set()).add(card_id)
            new = cards[successor]
            if new.fields.get("supersedes") != card_id:
                errors.append(
                    _error(
                        card.relative_path,
                        "superseded_by is not reciprocal with '{}'".format(successor),
                    )
                )

    for predecessor, successors in sorted(old_to_declared_successors.items()):
        if len(successors) > 1:
            errors.append(
                "supersession branching from '{}': {}".format(
                    predecessor, ", ".join(sorted(successors))
                )
            )
    for successor, predecessors in sorted(successor_to_declared_old.items()):
        if len(predecessors) > 1:
            errors.append(
                "supersession merging into '{}': {}".format(
                    successor, ", ".join(sorted(predecessors))
                )
            )

    colors: Dict[str, int] = {}
    stack: List[str] = []
    reported_cycles: Set[Tuple[str, ...]] = set()

    def visit(card_id: str) -> None:
        colors[card_id] = 1
        stack.append(card_id)
        successor = cards[card_id].fields.get("superseded_by")
        if isinstance(successor, str) and successor in cards:
            color = colors.get(successor, 0)
            if color == 0:
                visit(successor)
            elif color == 1:
                start = stack.index(successor)
                cycle = tuple(stack[start:] + [successor])
                if cycle not in reported_cycles:
                    reported_cycles.add(cycle)
                    errors.append(
                        "supersession cycle: {}".format(" -> ".join(cycle))
                    )
        stack.pop()
        colors[card_id] = 2

    for card_id in sorted(cards):
        if colors.get(card_id, 0) == 0:
            visit(card_id)


def _load_source_registry(
    brain_root: Path,
    errors: List[str],
) -> Tuple[Dict[str, Path], Set[str], Set[str], Set[str], Tuple[Path, ...]]:
    source_aliases: Dict[str, Path] = {}
    authorized_source_aliases: Set[str] = set()
    known_session_source_ids: Set[str] = set()
    session_source_ids: Set[str] = set()
    authorized_content_paths: List[Path] = []
    state_payload: Any = None

    roots_path = brain_root / "work" / "setup" / "source-roots.json"
    if roots_path.exists() or roots_path.is_symlink():
        try:
            payload = _read_private_json(roots_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(
                _error(
                    "work/setup/source-roots.json",
                    "cannot read source alias registry: {}".format(exc),
                )
            )
        else:
            if not isinstance(payload, dict):
                errors.append(
                    _error(
                        "work/setup/source-roots.json",
                        "source alias registry must be a mapping",
                    )
                )
            else:
                for alias, path in payload.items():
                    if (
                        not isinstance(alias, str)
                        or PATH_PART_RE.fullmatch(alias) is None
                        or not isinstance(path, str)
                        or not Path(path).expanduser().is_absolute()
                    ):
                        errors.append(
                            _error(
                                "work/setup/source-roots.json",
                                "every source alias must map an ASCII kebab id to an absolute path",
                            )
                        )
                        continue
                    source_aliases[alias.casefold()] = (
                        Path(path).expanduser().resolve(strict=False)
                    )

    state_path = brain_root / "work" / "setup" / "state.json"
    if state_path.exists() or state_path.is_symlink():
        try:
            payload = _read_private_json(state_path)
            state_payload = payload
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(
                _error(
                    "work/setup/state.json",
                    "cannot read session source registry: {}".format(exc),
                )
            )
        else:
            session_history = (
                payload.get("session_history", {})
                if isinstance(payload, dict)
                else {}
            )
            if not isinstance(session_history, dict):
                errors.append(
                    _error(
                        "work/setup/state.json",
                        "session_history must be a mapping",
                    )
                )
            else:
                for source_id in session_history:
                    if (
                        not isinstance(source_id, str)
                        or PATH_PART_RE.fullmatch(source_id) is None
                    ):
                        errors.append(
                            _error(
                                "work/setup/state.json",
                                "session source ids must use ASCII kebab-case",
                            )
                        )
                        continue
                    known_session_source_ids.add(source_id.casefold())

    if isinstance(state_payload, dict):
        state_name = state_payload.get("state")
        consents = state_payload.get("consents", [])

        def active_exact_consent(kind: str, scope: Iterable[str]) -> bool:
            normalized_scope = tuple(
                sorted(
                    str(Path(value).expanduser().resolve(strict=False))
                    for value in scope
                )
            )
            decision: Optional[str] = None
            if isinstance(consents, list):
                for consent in consents:
                    if (
                        isinstance(consent, dict)
                        and consent.get("kind") == kind
                        and isinstance(consent.get("scope"), list)
                    ):
                        candidate_scope = tuple(
                            sorted(
                                str(Path(value).expanduser().resolve(strict=False))
                                for value in consent["scope"]
                                if isinstance(value, str)
                            )
                        )
                        if candidate_scope == normalized_scope:
                            decision = consent.get("decision")
            return decision == "approved"

        areas = state_payload.get("areas", {})
        if isinstance(areas, dict):
            for area in areas.values():
                if not isinstance(area, dict):
                    continue
                in_progress_content = (
                    state_name == "CONTOUR_LOOP"
                    and area.get("execution") in {"in-progress", "closed"}
                    and isinstance(area.get("pre_fingerprints"), dict)
                    and bool(area["pre_fingerprints"].get("sha256"))
                )
                closed_content = (
                    area.get("execution") == "closed"
                    and area.get("coverage") == "content-reviewed"
                )
                if not (in_progress_content or closed_content):
                    continue
                for raw_path in area.get("paths", []):
                    if isinstance(raw_path, str):
                        authorized_content_paths.append(
                            Path(raw_path).expanduser().resolve(strict=False)
                        )

        scope_items = state_payload.get("scope", [])
        if isinstance(scope_items, list):
            for alias, source_root in source_aliases.items():
                for scope_item in scope_items:
                    if not isinstance(scope_item, dict):
                        continue
                    raw_path = scope_item.get("path")
                    if not isinstance(raw_path, str):
                        continue
                    scope_path = Path(raw_path).expanduser().resolve(strict=False)
                    if scope_path != source_root or scope_item.get("mode") != "content":
                        continue
                    if state_name == "CONTOUR_LOOP":
                        consent_scope = scope_item.get("consent_scope") or [
                            str(scope_path)
                        ]
                        if not active_exact_consent(
                            "content-audit",
                            consent_scope,
                        ):
                            continue
                    if any(
                        source_root == allowed
                        or source_root in allowed.parents
                        or allowed in source_root.parents
                        for allowed in authorized_content_paths
                    ):
                        authorized_source_aliases.add(alias)

        session_history = state_payload.get("session_history", {})
        if isinstance(session_history, dict):
            for source_id, source in session_history.items():
                if (
                    not isinstance(source_id, str)
                    or not isinstance(source, dict)
                    or source_id.casefold() not in known_session_source_ids
                ):
                    continue
                source_path = source.get("path")
                if not isinstance(source_path, str):
                    continue
                if state_name == "SESSION_HISTORY_AUDIT":
                    if active_exact_consent("session-history", [source_path]):
                        session_source_ids.add(source_id.casefold())
                elif (
                    source.get("execution") == "closed"
                    and source.get("coverage")
                    in {"distillates-reviewed", "transcripts-reviewed"}
                ):
                    session_source_ids.add(source_id.casefold())

    return (
        source_aliases,
        authorized_source_aliases,
        known_session_source_ids,
        session_source_ids,
        tuple(dict.fromkeys(authorized_content_paths)),
    )


def _build_catalog(
    brain_root_value: PathLike,
    *,
    canonical_search: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, _Card]]:
    errors: List[str] = []
    warnings: List[str] = []
    try:
        lexical_root = Path(brain_root_value).expanduser()
    except (TypeError, ValueError, OSError) as exc:
        return (
            {
                "ok": False,
                "cards_checked": 0,
                "canonical_cards": 0,
                "errors": ["invalid brain root: {}".format(exc)],
                "warnings": [],
            },
            {},
        )
    if lexical_root.is_symlink():
        return (
            {
                "ok": False,
                "cards_checked": 0,
                "canonical_cards": 0,
                "errors": ["brain root must not be a symbolic link"],
                "warnings": [],
            },
            {},
        )
    try:
        brain_root = lexical_root.resolve()
    except OSError as exc:
        return (
            {
                "ok": False,
                "cards_checked": 0,
                "canonical_cards": 0,
                "errors": ["cannot resolve brain root: {}".format(exc)],
                "warnings": [],
            },
            {},
        )
    if not brain_root.exists() or not brain_root.is_dir():
        return (
            {
                "ok": False,
                "cards_checked": 0,
                "canonical_cards": 0,
                "errors": ["brain root must be an existing directory"],
                "warnings": [],
            },
            {},
        )

    (
        source_aliases,
        authorized_source_aliases,
        known_session_source_ids,
        session_source_ids,
        authorized_content_paths,
    ) = _load_source_registry(brain_root, errors)
    paths, discovery_errors = _discover_card_paths(
        brain_root,
        roots=CANONICAL_ROOTS if canonical_search else SCAN_ROOTS,
        skip_example_paths=canonical_search,
    )
    errors.extend(discovery_errors)
    cards: Dict[str, _Card] = {}
    for path in paths:
        relative_path = path.relative_to(brain_root).as_posix()
        card_id = path.relative_to(brain_root).with_suffix("").as_posix()
        try:
            raw = _read_regular_utf8(path)
        except (OSError, UnicodeError) as exc:
            errors.append(_error(relative_path, "cannot read UTF-8 card: {}".format(exc)))
            continue
        if _contains_secret(raw):
            errors.append(
                _error(
                    relative_path,
                    "card contains a secret-like value",
                )
            )
            continue
        try:
            fields, body = _parse_frontmatter(raw)
        except FrontmatterError as exc:
            errors.append(
                _error(relative_path, "invalid frontmatter: {}".format(exc))
            )
            continue
        card = _Card(
            path=path,
            relative_path=relative_path,
            card_id=card_id,
            fields=fields,
            body=body,
            raw=raw,
        )
        if canonical_search and card.is_example:
            continue
        cards[card_id] = card
        _validate_path(card, errors)
        _validate_field_types(card, errors)
        _validate_dates(card, errors)
        _validate_sources(
            card,
            errors,
            source_aliases,
            authorized_source_aliases,
            authorized_content_paths,
            known_session_source_ids,
            session_source_ids,
        )
        _validate_receipts(
            card,
            errors,
            known_session_source_ids,
            session_source_ids,
        )
        _validate_origin(card, errors)
        _validate_closed_state(card, errors)
        _validate_example_state(card, errors)

    _validate_references(cards, errors)
    _validate_canonical_keys(cards, errors)
    _validate_supersession(cards, errors)

    unique_errors = sorted(set(errors))
    report = {
        "ok": not unique_errors,
        "cards_checked": len(cards),
        "canonical_cards": sum(1 for card in cards.values() if card.is_canonical),
        "errors": unique_errors,
        "warnings": sorted(set(warnings)),
    }
    return report, cards


def validate_cards(brain_root: PathLike) -> Dict[str, Any]:
    """Validate every card in the supported brain card roots.

    The function never repairs or rewrites cards.  All content and structural
    failures are returned in ``errors`` and set ``ok`` to ``False``.
    """

    report, _cards = _build_catalog(brain_root)
    return report


def _normalize_exact(value: str) -> str:
    return " ".join(value.casefold().split())


def _query_terms(query: str) -> List[str]:
    return [term.casefold() for term in WORD_RE.findall(query)]


def _searchable_text(card: _Card) -> Tuple[str, str, str]:
    title = card.fields.get("title")
    summary = card.fields.get("summary")
    tags = card.fields.get("tags")
    title_text = title if isinstance(title, str) else ""
    summary_text = summary if isinstance(summary, str) else ""
    tag_text = " ".join(tags) if isinstance(tags, list) else ""
    front_text = " ".join(
        value
        for value in (
            title_text,
            summary_text,
            tag_text,
            card.card_id,
            str(card.fields.get("canonical_key", "")),
            str(card.fields.get("project", "")),
        )
        if value
    )
    return title_text.casefold(), front_text.casefold(), card.body.casefold()


def _exact_keys(card: _Card) -> Set[str]:
    keys = {
        _normalize_exact(card.card_id),
        _normalize_exact(Path(card.card_id).name),
    }
    for field in ("title", "canonical_key"):
        value = card.fields.get(field)
        if isinstance(value, str) and value.strip():
            keys.add(_normalize_exact(value))
    return keys


def _follow_active_successor(
    card: _Card, cards: Dict[str, _Card]
) -> Optional[_Card]:
    current = card
    seen: Set[str] = set()
    while current.is_closed:
        if current.card_id in seen:
            return None
        seen.add(current.card_id)
        successor = current.fields.get("superseded_by")
        if not isinstance(successor, str) or successor not in cards:
            return None
        current = cards[successor]
    return current


def _stale_status(card: _Card, reference_date: dt.date) -> Tuple[bool, int]:
    threshold = card.fields.get("stale_after_days", 90)
    if type(threshold) is not int:
        threshold = 90
    if card.fields.get("volatile") is not True:
        return False, threshold
    fact_date = _real_date(card.fields.get("as_of"))
    if fact_date is None:
        return True, threshold
    return (reference_date - fact_date).days > threshold, threshold


def _result_payload(
    card: _Card,
    *,
    score: int,
    exact_rank: int,
    reference_date: dt.date,
    matched_via: Optional[str] = None,
) -> Dict[str, Any]:
    stale, stale_after_days = _stale_status(card, reference_date)
    result: Dict[str, Any] = {
        "card": card.card_id,
        "title": card.fields.get("title"),
        "type": card.fields.get("type"),
        "origin": card.fields.get("origin"),
        "summary": card.fields.get("summary", ""),
        "score": score,
        "exact": exact_rank > 0,
        "closed": card.is_closed,
        "stale": stale,
        "stale_after_days": stale_after_days,
        "as_of": card.fields.get("as_of"),
    }
    if matched_via is not None:
        result["matched_via_closed"] = matched_via
    return result


def _parse_search_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return dt.date.today()
    if type(value) is dt.date:
        return value
    return _real_date(value)


def search_cards(
    brain_root: PathLike,
    query: str,
    limit: int = 6,
    include_history: bool = False,
    as_of: Optional[Union[str, dt.date]] = None,
) -> Dict[str, Any]:
    """Search validated canonical cards with deterministic authority ordering.

    Search fails closed when any card is invalid.  Results never include
    ``inbox``, ``work``, ``system``, ``examples``, or ``example: true`` cards.
    A closed exact match contributes its active successor.
    """

    if not isinstance(query, str) or not query.strip():
        return {
            "ok": False,
            "query": query,
            "results": [],
            "errors": ["query must be a non-empty string"],
        }
    if type(limit) is not int or limit < 0:
        return {
            "ok": False,
            "query": query,
            "results": [],
            "errors": ["limit must be a non-negative integer"],
        }
    if type(include_history) is not bool:
        return {
            "ok": False,
            "query": query,
            "results": [],
            "errors": ["include_history must be true or false"],
        }
    reference_date = _parse_search_date(as_of)
    if reference_date is None:
        return {
            "ok": False,
            "query": query,
            "results": [],
            "errors": ["as_of must be a real YYYY-MM-DD date"],
        }

    validation, cards = _build_catalog(brain_root, canonical_search=True)
    if not validation["ok"]:
        return {
            "ok": False,
            "query": query,
            "results": [],
            "errors": validation["errors"],
        }

    terms = _query_terms(query)
    if not terms:
        return {
            "ok": False,
            "query": query,
            "results": [],
            "errors": ["query has no searchable words"],
        }

    normalized_query = _normalize_exact(query)
    ranked: Dict[str, Tuple[int, int, int, Optional[str]]] = {}

    for card in cards.values():
        if not card.is_canonical:
            continue
        if card.is_closed and normalized_query in _exact_keys(card):
            successor = _follow_active_successor(card, cards)
            if successor is not None and successor.is_canonical:
                ranked[successor.card_id] = (3, 10000, ORIGIN_PRIORITY.get(
                    str(successor.fields.get("origin")), 0
                ), card.card_id)

    for card in cards.values():
        if not card.is_canonical:
            continue
        if card.is_closed and not include_history:
            continue
        title_text, front_text, body_text = _searchable_text(card)
        exact_rank = 2 if normalized_query in _exact_keys(card) else 0
        title_hits = sum(title_text.count(term) for term in terms)
        front_hits = sum(front_text.count(term) for term in terms)
        body_hits = sum(body_text.count(term) for term in terms)
        score = (title_hits * 20) + (front_hits * 5) + body_hits
        if not exact_rank and score == 0:
            continue
        if exact_rank:
            score += 1000
        origin_priority = ORIGIN_PRIORITY.get(str(card.fields.get("origin")), 0)
        candidate = (exact_rank, score, origin_priority, None)
        current = ranked.get(card.card_id)
        if current is None or candidate[:3] > current[:3]:
            ranked[card.card_id] = candidate

    ordered = sorted(
        ranked.items(),
        key=lambda item: (
            -item[1][0],
            -item[1][2],
            -item[1][1],
            item[0],
        ),
    )
    effective_limit = min(limit, 6)
    results = [
        _result_payload(
            cards[card_id],
            exact_rank=rank[0],
            score=rank[1],
            reference_date=reference_date,
            matched_via=rank[3],
        )
        for card_id, rank in ordered[:effective_limit]
    ]
    return {
        "ok": True,
        "query": query,
        "as_of": reference_date.isoformat(),
        "include_history": include_history,
        "limit": effective_limit,
        "results": results,
        "errors": [],
    }


__all__ = ["search_cards", "validate_cards"]
