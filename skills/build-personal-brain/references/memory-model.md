# Memory model

Use this model for a private, cross-project memory that remains readable without
any particular database, agent host, or external version-control program.
Treat Markdown files plus the brain's built-in portable local history as the
canonical store. Treat search databases, embeddings, caches, generated
indexes, and any optional repository metadata as disposable derivatives.

## System boundaries

Keep three layers separate:

1. **Original systems** — repositories, documents, chats, tables, applications,
   and runtime state. They remain the source of current detail.
2. **Curated brain** — compact cards describing the user, projects, rules,
   decisions, relationships, and lessons, with provenance.
3. **Retrieval layer** — a registry plus optional exact or semantic index. It
   locates cards but never becomes the only copy of knowledge.

Do not move, rename, or rewrite original systems while building the brain. Do
not copy whole source trees into the brain. Store the distilled fact and a
source reference; open the original only when the task needs more detail or a
freshness check.

Keep the generated brain in a private folder separate from the public skill or
installer. The built-in history lives inside that folder and requires no
network account or external version-control tool. Create no publication or
sync destination by default.

## Canonical layout

```text
brain/
├── .brain-history/ built-in private revisions; never knowledge
├── profile/       stable user context and collaboration preferences
├── projects/      project and system cards, including scoped decisions
├── rules/         reusable active rules, one canonical lineage per rule
├── links/         explicit relations, people, organizations, and routing
├── journal/       compact session outcomes, not transcripts
├── inferred/      evidence-backed inferred rules and single observations
├── inbox/         unreviewed candidates and unresolved conflicts
├── system/        contract, schema, templates, validators, and state
└── work/          onboarding and maintenance state; never user knowledge
```

Exclude `work/**`, `inbox/**`, `_about.md`, synthetic examples, and system
documentation from normal knowledge retrieval. An inbox item can influence
work only after review promotes it into a canonical section.

Allow thematic subdirectories. Use ASCII `kebab-case` paths so every host and
search layer can address the same card reliably. Define a card identifier as
its path relative to the brain root without `.md`.

## Card schema

Every knowledge card is one Markdown file with YAML frontmatter and a readable
body.

Minimum:

```yaml
---
title: "Human-readable title"
type: rule
created: 2026-01-15
origin: stated
summary: "Short retrieval-oriented summary"
tags: [writing, review]
---
```

Required fields:

| Field | Values | Meaning |
|---|---|---|
| `title` | non-empty string | Human-readable identity |
| `type` | `project`, `rule`, `decision`, `person`, `entity`, `link`, `observation`, `journal`, `conflict` | Kind of card |
| `created` | `YYYY-MM-DD` | Date the card entered the brain |
| `origin` | `stated`, `imported`, `inferred`, `observation` | Authority and provenance class |

Useful optional fields:

| Field | Meaning |
|---|---|
| `summary` | One-line routing summary |
| `tags` | Search terms, not a substitute for structure |
| `project` | Primary project card identifier |
| `stated_in` | Session identifier or date of the direct instruction |
| `sources` | Structured references supporting imported knowledge |
| `imported_at` | Date an imported card was created |
| `volatile` | `true` when the fact changes over time |
| `as_of` | Date on which a volatile fact was current |
| `receipts` | Short, dated evidence for an inferred card |
| `valid_from` | Date the fact became true in the real world |
| `valid_to` | Date the fact stopped being true |
| `closed_at` | Date the brain recorded closure |
| `supersedes` | Identifier of the version replaced by this card |
| `superseded_by` | Identifier of the active replacement |

Put one independently maintainable claim on one card. Split a composite claim
when its parts have different origins or validity periods.

### Source references

Use structured source entries:

```yaml
sources:
  - file: "alpha-work:docs/decisions.md#Heading"
  - git: "alpha-repo@revision-id:docs/decisions.md#L20"
  - sheet: "planning-book:Roadmap!A2:D12"
  - chat: "support-export:message=123"
  - session: "codex-history:stable-session-id"
```

Require at least one source plus `imported_at` for `origin: imported`. Prefer a
stable heading, line, record, or message anchor. Never place secret values in a
card or source reference.

`alpha-work`, `alpha-repo`, `planning-book`, and `support-export` are safe
source aliases, not filesystem paths. Resolve them through the private
machine-local source registry under `work/setup/`. Store exact approved roots
only in that operating registry. In a session reference, the prefix before
the first `:` is the stable source ID recorded for the approved session
source. Once assigned, an alias remains bound to that canonical root; adding
or reordering sources must never reuse it for a different root. Never infer
an alias, expose an absolute user path in a card, or follow an alias whose
current resolution is outside approved scope. A known alias is not enough:
file-like provenance requires active approved content coverage for that root.
A known session ID likewise requires reviewed session content; names-only or
metadata-only discovery cannot authorize a claim or receipt.

### Receipts

Use short receipts only for evidence-backed inference:

```yaml
receipts:
  - session: "codex-history:session-a"
    date: 2026-01-12
    quote: "A short exact excerpt supporting the inference."
  - session: "codex-history:session-b"
    date: 2026-01-15
    quote: "An independent supporting excerpt."
```

Require at least two independent sessions for an inferred rule and three for a
weak or high-risk signal. Keep every quote under the configured receipt limit.
An observation needs no inference threshold because it is not an instruction.

## Authority model

Apply this order within memory:

```text
stated > imported > inferred > observation
```

- `stated`: the user directly taught, chose, or approved it.
- `imported`: a source says it; the user has not necessarily endorsed it.
- `inferred`: repeated evidence supports it; it yields to direct instruction.
- `observation`: a single signal; consider it, but never enforce it.

Platform safety rules and the user's current explicit instruction outrank
memory. An authoritative live project file outranks a stale imported summary.
Do not silently choose between two plausible active canonical rules. Record a
conflict and ask for a decision.

## Supersession and time

Never make an outdated rule compete with its replacement.

For `A → B`:

1. Set `A.valid_to`, `A.closed_at`, and `A.superseded_by`.
2. Set `B.valid_from` and `B.supersedes`.
3. Record a compact transition reason in both bodies.
4. Save both cards in one exact local-history revision.

Resolve chronology by real-world validity, not file creation order. A historical
import can therefore have `valid_from` earlier than `created`.

Normal retrieval must follow `superseded_by` until it reaches an active card.
Stop and report a missing link, cycle, or multiple active descendants. Use a
closed card directly only for a historical question.

Retirement without replacement is valid: close the card and record why, but do
not invent a successor.

## Selective retrieval

Use progressive disclosure:

1. Read the short runner contract.
2. Read the compact active profile core.
3. Identify the project from, in order: explicit user name, current working
   directory, repository remote, and the registry.
4. Search filenames, frontmatter, summaries, and tags before reading bodies.
5. Select the smallest useful packet, normally two to six active cards.
6. Follow only links required by the task.
7. Open an original source only when the card is insufficient, volatile, or
   disputed.
8. Expand again only when evidence demands it.

Compose context instead of forcing tasks into one category:

```text
compact user context
  + current project
  + relevant reusable rules
  + current decisions and relationships
  + fresh source detail when needed
```

Keep routine retrieval invisible. Surface it when sources conflict, volatile
data is stale, a supersession chain is broken, or the action has high
consequence.

Do not preload the whole brain or run a long universal checklist for every
request. Context economy is part of correctness: irrelevant memory can change
an answer as surely as missing memory.

## Registry and indexes

Maintain one compact registry that maps projects and capabilities to their
canonical cards and safe source aliases. Treat a working directory or an
optional source repository identity as a routing hint, not as a security
boundary.

Start with deterministic local filename/frontmatter search. Add a semantic
index only after a measured retrieval test shows material benefit. Require any
index to:

- index only approved canonical sections;
- exclude closed cards from current-result defaults;
- exclude `work`, `inbox`, examples, raw sources, and secrets;
- preserve card identifiers and provenance;
- rebuild from files without loss;
- work offline by default or disclose every network transfer;
- pass the same multilingual and privacy tests as exact search.

## Write invariants

Before changing knowledge:

1. Require an explicit maintenance intent.
2. Verify the built-in local history and require a clean memory tree.
3. Resolve the exact target cards and active lineage.
4. Enumerate intended paths.
5. Reject secret-bearing content.
6. Apply the smallest coherent change.
7. Validate schema, links, provenance, supersession, and exclusions.
8. Review the complete change and its actual path set.
9. Require the declared path set to equal the actual changed path set exactly,
   then save one local-history revision with a non-empty summary and reason.
10. Rebuild or verify derivative indexes.

Fail closed on ambiguous canon, broken lineage, missing provenance, concurrent
changes, an unexpected changed path, or a secret-like value.

## Built-in portable local history

The ordinary Markdown tree is always readable on its own. `.brain-history/`
adds dependency-free, content-addressed revisions for integrity, review, and
undo. It is private operating data, excluded from retrieval, indexing, source
aliases, and ordinary card links.

For every semantic save:

1. verify history integrity, the expected current revision, and a clean
   starting state;
2. acquire the single-writer lock;
3. apply only the enumerated card changes;
4. validate the resulting brain;
5. compare the actual added, modified, and deleted relative paths with the
   declared path set; reject a missing, duplicate, excluded, unsafe, or
   unexpected path;
6. store the exact file bytes and modes in content-addressed objects;
7. store a full manifest and one revision containing the parent revision,
   exact changed paths, non-empty summary, and non-empty reason;
8. publish the new head atomically, then verify that the tree is clean.

An interrupted save must not publish a partial revision. Git may exist in an
original source project and its revision may be useful provenance, but Git is
not required for the brain and is not its canonical history.

Undo is a new forward revision, never a backward pointer move or history
rewrite:

1. require a clean, valid current state;
2. choose an existing target revision;
3. preview the exact files that would be added, modified, and deleted;
4. bind a confirmation token to the current head, target revision, and exact
   preview;
5. after explicit confirmation, apply using that same token;
6. reject the action if the token is stale, the current head changed, or the
   working tree is no longer clean;
7. restore exact bytes and recorded modes transactionally;
8. publish a new `undo` revision whose parent is the former current head and
   whose manifest matches the requested prior state.

If restoration fails, roll the working tree back to its pre-undo state and do
not advance history.

Local history can retain private information from older revisions. Deleting or
redacting a current Markdown file and saving again does not erase its earlier
bytes. Ordinary retirement, replacement, and undo preserve history by design.
A privacy-erasure request therefore requires a separate exact purge plan that
covers current cards, reachable history objects, indexes, exports, sync copies,
and backups; explains the loss of recovery points; and receives explicit
destructive approval. Never promise secure erasure through an ordinary save.
