# Knowledge strength and history

## Strength

Use this order:

1. `stated` — directly said or confirmed by the owner;
2. `imported` — extracted from a permitted source with a citation;
3. `inferred` — supported by multiple independent session receipts;
4. `observation` — a single signal.

Automation may add an observation or propose an inference. It may not silently
replace a `stated` card.

## Conflicts

Do not merge incompatible claims into a synthetic answer.
Create one `type: conflict` card in `inbox/` containing:

- each option and its source;
- what appears to be used in practice;
- the effect of leaving it unresolved;
- an empty owner-decision section.

An unresolved conflict is not current knowledge.

## History

New information does not erase old information.
Close the version that stopped being true, connect both directions, and use
the active version for ordinary tasks.

Determine history by the time the fact was true, not merely by file creation
time.

This knowledge history is distinct from the built-in file revision history:

- card validity answers what was true and when;
- `.brain-history/` preserves exact saved states and supports forward undo.

Undoing files does not automatically change real-world validity, and closing a
card does not delete its older bytes from local history. Treat privacy erasure
as a separate explicitly approved purge across current cards, built-in
history, indexes, copies, sync destinations, and backups.
