# Maintenance

Maintain memory as a small, reviewable knowledge repository. Do not let routine
capture, search indexes, or agent summaries mutate canon indirectly.

## Intent vocabulary

Treat these as distinct operations:

| User intent | Operation |
|---|---|
| “Remember this”, “learn this”, “from now on” | Add or update active stated knowledge |
| “Replace A with B”, “we no longer do A; do B” | Supersede A with B |
| “Forget/stop using A” | Clarify retirement versus irreversible erasure |
| “That was wrong; it was never true” | Correct provenance/history without pretending it was a normal policy change |
| “Refresh this number/status” | Add a newer volatile snapshot |

Do not treat a temporary task constraint as durable memory. When scope is
unclear, ask whether the instruction is user-global, reusable, project-scoped,
or task-only.

## Common transaction

Use this transaction for every canonical write:

1. Confirm explicit maintenance intent.
2. Check that the brain and built-in history are valid and the memory tree is
   clean.
3. Resolve the current project, target card, and active supersession lineage.
4. Check for a concurrent writer or a newer saved revision.
5. Enumerate exact intended paths.
6. Classify origin, scope, validity, volatility, and source requirements.
7. Screen proposed content for secrets and unnecessary private detail.
8. Apply the smallest coherent change.
9. Validate frontmatter, links, provenance, lineage, exclusions, and core size.
10. Inspect the complete change and actual added, modified, and deleted paths.
11. Require the declared paths to equal the actual paths exactly, then save one
    local-history revision with a non-empty summary and reason.
12. Verify or rebuild derivative indexes.
13. Run focused retrieval tests for the changed knowledge.

If history integrity fails, the tree is dirty, the target is ambiguous, a
source is unavailable, a secret appears, or the change includes an unexpected
path, stop without saving.

After an interrupted write, do not delete lock or transaction files by hand.
Inspect the outer operation lock and clear it only when it is a proven stale
local lock, using its preview and current token. Then use the built-in
history recovery preview and current token. A clean one-revision mismatch
between history and durable workflow state uses the separate reconciliation
preview and token. Any longer, dirty, or ambiguous mismatch remains
failed-closed.

Recommended reason form:

```text
<scope>: <change> — reason: <why>
```

## Remember

Use `remember` only for a direct instruction or decision that should survive
the current task.

### New knowledge

1. Choose the narrowest correct section and scope.
2. Search for an existing active card before creating another.
3. Create one independently maintainable claim.
4. Set `origin: stated` and `stated_in`.
5. Set `valid_from` to the real-world start date when known; otherwise use the
   recording date without claiming historical precision.
6. Add a compact summary and only the links needed for retrieval.
7. Preserve exact wording when wording itself is the rule.

Do not copy a full transcript into the card. Do not label a fact `stated`
merely because it appears in a file.

### Existing compatible knowledge

Update in place only when the meaning and historical validity do not change,
for example:

- fix a typo;
- add a missing source anchor;
- improve a summary without changing the rule;
- add a relationship already implied by the same decision.

Use a new superseding card when the operative meaning changes.

### Conflict discovered during remember

If new text conflicts with an active card but the user did not clearly replace
it:

1. do not choose silently;
2. describe the two candidate meanings briefly;
3. ask whether the new instruction replaces, narrows, or applies beside the
   old one;
4. record an inbox conflict only if resolution cannot happen immediately.

## Replace

Treat explicit replacement as one atomic history transaction.

Given active card `A` and replacement `B`:

1. Verify that `A` is the active end of its lineage.
2. Create `B` with the proper origin and `valid_from`.
3. Set `B.supersedes` to `A`.
4. Set `A.valid_to`, `A.closed_at`, and `A.superseded_by`.
5. Add a compact transition note to both cards:

   ```text
   Previously: A.
   Now: B.
   Reason: <recorded reason or “reason not recorded”>.
   ```

6. Validate that the lineage has one active end, no cycle, and reciprocal
   links.
7. Save both sides together in one exact revision.
8. Test a current query and a historical query.

If the user corrects a card that was never true, do not manufacture a period of
validity. Mark the incorrect entry as corrected/invalid with an explanation,
link the correction, and preserve only the minimum audit trail allowed by the
privacy policy.

## Forget

“Forget” has two materially different meanings. Clarify unless the wording is
unambiguous.

### Retire: stop applying it

Use retirement when the user wants the brain to stop using a fact or rule but
does not require data destruction.

1. Resolve the exact active card.
2. Set `valid_to` and `closed_at`.
3. Record the reason.
4. Leave `superseded_by` empty because there is no replacement.
5. Remove or update registry routes that would present it as active.
6. Preserve the closed card and built-in local history.
7. Verify that current retrieval excludes it and historical retrieval can
   still explain it.

### Erase: remove the information

Use erasure for privacy, legal, safety, or explicit data-destruction intent.
Explain that deleting a Markdown file does not remove older objects in
`.brain-history/`, indexes, copies, exports, sync destinations, or backups.

Before destructive action:

1. Identify the exact information, not a broad directory.
2. Locate every canonical card, source excerpt, journal mention, reachable
   local-history object, derived index, cache, copy, sync destination, export,
   and managed backup in scope.
3. Separate original systems from brain-owned copies; request new authority
   before changing an original source.
4. Produce a purge plan and recovery consequences.
5. Obtain explicit confirmation for the exact destructive scope.
6. Remove working-tree copies and rebuild indexes.
7. Purge or replace the built-in history store only when explicitly authorized
   and operationally safe; ordinary save and undo are not erasure tools.
8. Coordinate deletion from every copy, sync destination, export, and backup
   under its retention policy.
9. Rotate any exposed secret; deletion alone does not make it safe.
10. Verify absence with content and index scans without echoing the data.
11. Record a non-sensitive erasure receipt outside the purged content when
    policy requires an audit trail.

Never present ordinary retirement as secure erasure. Never run a broad
recursive deletion or history purge from an ambiguous “forget that”.

## Imported knowledge

For source-backed refresh:

1. Check the canonical source and its freshness.
2. Preserve the previous snapshot when the old value was historically true.
3. Set the new `as_of` independently from `imported_at`.
4. Supersede the old snapshot when the new value changes current truth.
5. Keep source anchors stable and verify they resolve.
6. Do not promote imported knowledge to stated without direct approval.

If the source disappears, mark provenance as broken or stale. Do not convert
the last cached value into timeless truth.

## Inferred knowledge and observations

- Add an observation for a single low-risk signal; never enforce it.
- Promote to inferred only after the configured independent-evidence threshold.
- Keep short dated receipts and stable session identifiers.
- Demote or close an inference when its evidence is invalidated.
- Let any conflicting direct user instruction win immediately.
- Never let automated mining update or close a stated card.

Keep capture stages separate:

```text
raw session → mechanical candidate → reviewed draft → canonical card
```

Reject a candidate that contains secrets, lacks a clear object, duplicates
canon, or cannot establish provenance.

## Inbox and conflicts

Inbox is a queue, not long-term knowledge.

For each item, choose exactly one outcome:

- promote to a new canonical card;
- merge into an existing card;
- resolve through supersession;
- reject as duplicate, unsafe, unsupported, or irrelevant;
- keep blocked with a concrete question and owner.

When resolved, update canonical cards and remove the inbox item in the same
local-history revision. Do not retrieve an inbox item as a current rule.

## Profile core

Keep the always-on core small and safe:

- include stable roles, collaboration preferences, and routing pointers;
- exclude contact details, credentials, volatile metrics, and deep project
  instructions;
- link to on-demand cards for detail;
- enforce a configured size limit;
- rerun all host visibility and context-budget tests after changing it.

Update the core only when the new fact genuinely belongs in nearly every
substantive task.

## Registry maintenance

After adding, moving, closing, or renaming a project card:

1. update its registry entry;
2. update all reciprocal links in the same saved revision;
3. verify source aliases and optional repository-identity hints;
4. detect duplicate routes and ambiguous aliases;
5. test explicit-name, working-folder, and optional repository-identity
   routing;
6. ensure a closed project is not selected as active.

Do not infer a security boundary from routing metadata.

## Validation and health cadence

Run on every write:

- schema and frontmatter validation;
- link and reciprocal-supersession validation;
- active-lineage uniqueness;
- provenance and receipt validation;
- forbidden-section/index checks;
- secret-value scan;
- exact declared-path and complete-change inspection;
- focused retrieval regression.

Run periodically:

- full acceptance fixture suite;
- real retrieval gold set;
- stale volatile-card report;
- unresolved conflict and inbox aging report;
- orphan, duplicate, and broken-source scan;
- compact-core size check;
- connection health for every selected host;
- index rebuild test;
- backup restore drill.

Use a machine-readable system state for schema, migration, onboarding, and
connection versions. Generate human status from it so narrative documentation
cannot silently lag behind implementation.

## Backup and restore

Keep at least:

- the complete brain folder, including `.brain-history/`;
- one private backup under the user's control;
- documented index rebuild steps;
- a tested connection-state backup that contains no secret values.

To restore:

1. restore canonical files and the built-in local history;
2. validate before connecting any host;
3. verify history integrity, the current head, and clean state;
4. rebuild indexes from files;
5. render the new absolute root into bounded host blocks;
6. run the full connection and retrieval canaries.

Never restore from a search database alone.

## Migration

For a schema or layout change:

1. version the old and new schemas;
2. inventory affected cards without mutation;
3. generate a deterministic migration plan;
4. back up and require valid history plus a clean tree;
5. migrate a synthetic fixture first;
6. migrate in bounded local-history revisions;
7. preserve card identifiers or provide complete redirects;
8. validate and run the gold set;
9. keep a tested rollback path until acceptance passes.

Do not combine schema migration with unrelated knowledge edits.
