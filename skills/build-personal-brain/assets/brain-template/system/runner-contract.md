# Cross-project memory contract

This file is operating material for an agent. It is not user knowledge.

## Start small

1. For a substantive task, read `profile/owner.md` first.
2. Read `links/brain-index.md` to choose a project or area.
3. Load only a small relevant packet, normally two to six cards.
4. Never load the whole library into context.
5. Do not treat `system/`, `work/`, `examples/`, or unresolved `inbox/`
   content as current knowledge.

## Choose current knowledge

1. Current explicit user instructions override stored memory.
2. Prefer origins in this order:
   `stated > imported > inferred > observation`.
3. Do not apply a closed card as current. Follow `superseded_by` to the
   active version.
4. If the chain is missing, cyclic, or ambiguous, stop and report the issue.
5. For `volatile: true`, check `as_of` before using the fact.
6. Open an original source only when the compact card is insufficient and
   the path is permitted and non-secret.

## Route to a project

Use this order:

1. an explicit project name from the user;
2. the current working folder or its repository name;
3. `links/brain-index.md`;
4. one short clarifying question.

Folder location is a hint, not a hard project boundary.

## Speak normally

Keep routine retrieval invisible. Explain it only for:

- conflicting sources;
- stale changing data;
- money, external publication, deletion, or another high-cost action;
- a required permission decision.

If a reason is unknown, say so. Never invent it.

## Record only explicit memory

Write only when the user explicitly asks to remember, learn, correct, replace,
or forget something.

Before writing:

1. read `system/runtime/guides/maintenance.md` and
   `system/runtime/guides/safety-rules.md`;
2. use `system/runtime/scripts/brainctl.py` for validation, exact saves,
   search, history inspection, two-step undo, and token-bound crash recovery;
3. validate the built-in local history and require a clean memory tree;
4. identify the exact target cards;
5. read the schema, status rules, conventions, and relevant template;
6. reject secret-bearing content and forbidden paths;
7. distinguish a new fact, a scoped exception, a replacement, and a request
   to physically erase sensitive data.

For a replacement, close the old card and create the new card in one change.
Do not delete history unless the user explicitly requests sensitive-data
purging and understands the consequences.

Validate the complete change, save one semantic history point with a reason,
and only then confirm that the memory was updated.

The declared relative path set must exactly equal the actual added, modified,
and deleted paths. Save through `.brain-history/` and atomically publish one
new revision; no external version-control program is required.

For ordinary undo, first preview the exact changes and obtain confirmation tied
to the preview token. Reject a stale token. Restore exact bytes and modes, then
create a new forward `undo` revision; never move history backward or rewrite
older revisions.

Older history objects may retain information removed from current cards. An
ordinary correction, retirement, deletion, save, or undo is not privacy
erasure. Explain and separately authorize any purge of history, indexes,
copies, sync destinations, or backups.

## Fail closed

Stop rather than guess when:

- memory is missing or unreadable;
- the memory tree contains unexpected changes;
- a source is secret-bearing;
- metadata or evidence is invalid;
- a version chain is broken;
- current sources conflict;
- an operation would exceed the user's approved folders or actions.
