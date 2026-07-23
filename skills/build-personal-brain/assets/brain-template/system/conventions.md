# Library conventions

## Layout

- `profile/` — compact owner context;
- `projects/` — projects, systems, goals, state, and project decisions;
- `rules/` — one active canonical version of each reusable rule;
- `links/people/`, `links/entities/`, `links/relations/`,
  `links/decisions/` — people, organizations, relationships, and global
  decisions;
- `journal/YYYY/` — compact session or audit summaries;
- `inferred/` — supported inferences and single observations;
- `inbox/` — unresolved conflicts and temporary incoming items;
- `system/` — operating rules and templates;
- `work/` — setup progress and audit artifacts;
- `examples/` — synthetic examples only.

`system/`, `work/`, `examples/`, and unresolved `inbox/` are not current
knowledge.

## Content

- Keep cards compact and readable.
- Prefer the owner's existing synthesis, index, ledger, or accepted document
  over raw exports.
- Keep full source files, transcripts, tables, and drafts in their original
  systems.
- Store a concise conclusion plus a resolvable source reference. Use a safe
  source-root alias or approved session source ID, never an absolute user path
  in a card.
- Write `Reason not recorded` instead of reconstructing one.
- Projects may link across all areas; a folder is not an isolated memory
  container.

## History

Markdown plus `.brain-history/` is the canonical store. History is built into
this folder and requires no external version-control program, network account,
or remote.
One semantic memory change equals one local history revision with a non-empty
summary and reason. Do not mix unrelated rule updates, bulk formatting, and
inbox resolution.

Before saving, verify history integrity and a clean starting state, enumerate
the exact intended relative paths, validate the result, and require those paths
to equal the actual added, modified, and deleted paths. Publish the new
revision atomically and confirm that the tree is clean.

When replacing A with B:

1. close A;
2. create B;
3. connect both directions;
4. record the compact transition and reason;
5. update both sides in one local-history revision.

Undo first produces an exact add/modify/delete preview and a confirmation token.
Apply only that unchanged token to a clean tree. Undo restores exact saved bytes
and modes and creates a new forward revision; it never rewrites older history.

Old history objects may retain information removed from current cards.
Retirement, replacement, ordinary deletion, save, and undo are not privacy
erasure. Full erasure requires a separate approved purge plan for history,
indexes, copies, sync destinations, and backups.

## Inbox

When an inbox item is resolved, create or update the final card and remove the
inbox item in the same semantic change. Do not use inbox as a permanent
archive.
