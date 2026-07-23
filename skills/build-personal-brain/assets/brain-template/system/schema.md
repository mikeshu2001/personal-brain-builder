# Card schema

A knowledge card is one Markdown file with YAML frontmatter.
Its identity is its path relative to the brain root without `.md`.

## Required fields

```yaml
---
title: "Human-readable title"
type: project
created: 2026-01-01
origin: imported
---
```

Every knowledge card requires:

- `title`: non-empty string;
- `type`: `project`, `rule`, `decision`, `person`, `entity`, `link`,
  `observation`, `journal`, or `conflict`;
- `created`: `YYYY-MM-DD`;
- `origin`: `stated`, `imported`, `inferred`, or `observation`.

One card has exactly one origin. Split mixed-origin claims into separate cards.

## Origin evidence

### Stated

Use `origin: stated` only for a direct user instruction, decision, or explicit
confirmation. Add `stated_in` with a date or session identifier when known.

### Imported

Use `origin: imported` for a fact extracted from a permitted source.
Require:

```yaml
imported_at: 2026-01-01
sources:
  - file: "root-alias:path/to/file.md#Section"
```

Supported source keys are `file`, `git`, `sheet`, `chat`, and `session`.
Use source-root aliases instead of absolute paths:

```yaml
sources:
  - file: "alpha-work:docs/decision.md#Section"
  - git: "alpha-repo@revision-id:docs/decision.md#L20"
  - sheet: "planning-book:Roadmap!A2:C10"
  - chat: "support-export:message=123"
  - session: "codex-history:stable-session-id"
```

Resolve `alpha-work`, `alpha-repo`, `planning-book`, and `support-export`
through the private source registry under `work/setup/`. For `git`, the
optional `@revision-id` follows the registered alias. In a session value, the
prefix before the first `:` must be the stable ID registered for an explicitly
approved session source. Reject
an unknown alias, a public absolute path, a target outside its approved real
root, or a secret-bearing target. `git` is only an optional provenance form
for an original source project; it is not required for this memory's history.
Once assigned, an alias remains bound to the same canonical source root.
Adding or reordering sources must never reuse it for a different root.

### Inferred

Use `origin: inferred` only with dated verbatim receipts from at least two
independent sessions, or three for a weak or high-risk signal:

```yaml
receipts:
  - session: "codex-history:stable-session-id"
    date: 2026-01-01
    quote: "Verbatim quote under 200 characters"
```

Add `risk: high` when an inferred claim could materially affect money,
privacy, access, publication, deletion, or another costly action. High-risk
inferences require three independent receipts.

### Observation

Use `origin: observation` for a single contextual signal. It is never an
instruction.

## Freshness

Changing facts require:

```yaml
volatile: true
as_of: 2026-01-01
stale_after_days: 30
```

`as_of` is the date the information describes, not the import date.
`stale_after_days` is optional and is allowed only for a volatile card.

## Version replacement

Old active card:

```yaml
canonical_key: "rules/output-style"
valid_to: 2026-02-01
closed_at: 2026-02-01
superseded_by: rules/new-rule
```

New active card:

```yaml
canonical_key: "rules/output-style"
valid_from: 2026-02-01
supersedes: rules/old-rule
```

Both sides of a replacement require the same non-empty `canonical_key`.
Only one active canonical card may use a given key.

Both bodies record:

`Previously A → now B → reason`, or `Reason not recorded`.

## Names and links

- Use ASCII `kebab-case` paths.
- Use internal links as `[[projects/example]]`.
- Store `project`, `supersedes`, and `superseded_by` as brain-relative card
  identifiers without `.md`.
- A reference must point to a card, not a directory.
- Synthetic examples require `example: true` and never participate in
  retrieval.

## Local history

Cards and `.brain-history/` together form the canonical store. The history
contains content-addressed exact bytes, modes, manifests, and forward
revisions. It is operating data, not a card or source, and must never enter
ordinary retrieval.

Every save requires a non-empty summary, non-empty reason, expected current
revision, and an exact deduplicated set of safe brain-relative changed paths.
The save fails if actual added, modified, or deleted paths differ from that
set. Undo requires a preview token tied to the current head, chosen target, and
exact change preview, then records a new forward `undo` revision.

History may retain older private bytes after a current card is removed or
redacted. Ordinary save and undo do not erase them.
