# Connection protocol

Connect multiple agent hosts to one private brain through a short, bounded
bootstrap. Keep all substantive behavior in the brain's runner contract so host
configuration contains only a pointer and can be repaired uniformly.

## Goals

The connection must be:

- additive: preserve all user-owned host instructions;
- bounded: own exactly one marked region;
- idempotent: repeated installation produces identical bytes;
- reversible: removal deletes only the owned region;
- portable: discover paths instead of embedding a username or machine inode;
- atomic per target: either one selected host file is changed completely or
  that file is restored;
- fail-closed: stop on ambiguity or external drift;
- testable: verify both file state and actual agent visibility.

Connecting a host grants no broader filesystem access. If the agent cannot read
the brain, request access only to the configured brain root.

## Canonical bootstrap

Use generic, unique markers:

```markdown
<!-- START PERSONAL BRAIN -->

## Personal brain

The private cross-project memory root is `${BRAIN_ROOT}`.
For substantive tasks, read `${BRAIN_ROOT}/system/runner-contract.md` first.
Load the compact active profile core before a small task-specific packet.
Never treat `work/**`, `inbox/**`, system files, or examples as user knowledge.
Never apply a closed card as current; follow its replacement chain.
Change memory only for an explicit remember, replace, retire, or erase request,
using the contract's validation and exact reasoned local-save flow.
Current explicit instructions and host safety rules win over retrieved memory.

<!-- END PERSONAL BRAIN -->
```

Render `${BRAIN_ROOT}` to an absolute, normalized path at installation. Do not
expand it to a broad parent directory. Keep the block short enough to remain in
every host's initial context.

Use host-specific syntax only when required. Keep the semantic content
identical across adapters.

## Target discovery

For each selected host:

1. Ask the adapter for the documented global instruction location.
2. Show that one exact location and obtain permission to read it for a
   no-change preview.
3. Resolve the path without following an untrusted symlink.
4. Create a missing regular file only after previewing the target.
5. Record its baseline hash, permissions, owner, and relevant metadata in
   private installation state.
6. Never bake machine-specific metadata into the public repository.

Do not scan a home directory for plausible instruction files and mutate all
matches. Connect only hosts the user selected.

## Managed-region states

Classify every target before writing:

| State | Meaning | Action |
|---|---|---|
| `missing` | no markers | offer one insertion |
| `canonical` | exactly one byte-correct block | no-op |
| `managed-drift` | one well-formed block for this brain with changed owned content | offer a fresh preview that repairs the block only |
| `outside-change` | user-owned content before or after the block changed | preserve every outside byte and bind a fresh preview to the complete current file |
| `malformed` | duplicate, reversed, nested, or partial markers | stop and show the problem |
| `foreign` | a managed block belongs to another brain | stop; never replace it |
| `unsafe-target` | symlink, hard link, wrong type, unexpected owner/mode, or detected credential-like value | stop before preview or backup |

Never interpret two marker pairs as permission to choose one. Never delete text
outside the exact line-bounded managed region.

## Installation transaction

Process selected targets one at a time so every approval, preview, failure, and
verification remains understandable.

For each target:

1. Validate the brain root, runner contract, compact core, and registry.
2. Register that one exact selected target in private state.
3. Obtain exact read permission, reject a detected credential-bearing target
   without copying it, and produce a no-change preview.
4. Show the exact target path and complete bounded diff, then obtain separate
   write approval for that target.
5. Re-read the target and reject any identity or byte change after preview.
6. Save the original bytes in the brain's private recovery directory and
   write a pending transaction record.
7. Preserve mode, newline convention, and all bytes outside the managed
   region.
8. Ask the owner to close the program that may rewrite this file, recheck its
   exact identity and bytes, and atomically replace the one target.
9. Re-read and verify the exact expected bytes, then complete the transaction
   record and private connection state.
10. Run a fresh-conversation check and mark the target verified only after it
    succeeds.

If one target fails, roll back that target, stop before the next one, and
report earlier independently verified targets honestly. Do not describe a
group as fully connected until every selected target is verified.

Do not use broad recursive permissions, shell globs, or unresolved environment
variables to identify targets.

## Idempotence and concurrent changes

Applying against canonical state must produce no diff and no metadata change.
A repair may rewrite only one well-formed managed region belonging to the same
brain, after a new preview and exact write approval.

When user-owned content changes outside the block:

1. Leave the target untouched.
2. Create a fresh preview from the complete current bytes.
3. Show that the proposed result preserves all outside text.
4. Require a new exact write approval and preview token.
5. Re-read immediately before writing and reject any further hash mismatch.

The operation lock prevents two builder actions from writing together. It
cannot force an unrelated program to honor that lock. Keep the host program
closed during apply or removal; if an uncooperative process changes the file
in the final check-and-replace window, stop on the next verification and do
not overwrite the newer outside change during rollback.

## Runtime protocol

For a substantive task, every connected agent must:

1. Read the runner contract.
2. Read the active compact core.
3. Route to the current project.
4. Retrieve a small active packet.
5. Execute under current user, host, and project instructions.

Skip brain retrieval for clearly context-free operations. Do not turn the
bootstrap into a visible ritual or ask the user to name every relevant source.

Agents must share the files, not hidden per-agent summaries. If a host provides
its own memory feature, treat it as a cache or capture source, never as a
higher-authority replacement for the curated brain.

## Health checks

Use the implemented operations:

```text
connect preview          show the exact bounded addition or repair
connect apply            apply only the current preview token
connect remove-preview   show the exact bounded removal
connect remove           remove only with the current preview token
connect recover-preview  show interrupted transactions without changing them
connect recover          roll back reviewed interrupted transactions
validate                 verify the brain and every recorded live target
record connection        record fresh-conversation verification
```

`validate` plus the fresh-conversation test must verify:

- the brain root exists and is readable;
- the contract and compact core validate;
- every target has exactly one canonical block;
- target metadata remains inside policy;
- the rendered root is exact;
- no target points at another user's or missing brain;
- each selected agent can answer a canary question from a synthetic card;
- a project-local canary routes to the correct project card.

File visibility is not enough: run at least one fresh-session behavior test per
host because a host may ignore or cache global instructions.

## Removal

Removal must:

1. classify and validate the one selected target;
2. refuse malformed markers;
3. remove only the managed region and its owned terminal newline;
4. preserve all surrounding bytes and metadata;
5. verify the brain itself was not deleted or modified;
6. retain a recoverable local snapshot until post-removal checks pass.

Do not remove the private brain when disconnecting an agent. Brain deletion is
a separate destructive operation.

## Recovery

Use the smallest recovery that restores invariants.

### Interrupted write

- Detect a temporary file or transaction journal.
- Compare live targets with pre-write hashes.
- Complete only if all remaining preconditions still hold.
- Otherwise roll back from private snapshots only when the current target is
  still exactly the transaction's known before or after version. Preserve any
  third version and require owner review.

### Missing or stale block

- Revalidate the brain.
- Insert a missing block or repair only a single well-formed managed region.
- Do not touch outside drift automatically.

### Moved brain

- Do not silently rewrite the memory's identity, saved source roots, builder
  pointer, or connected host files.
- Restore to the same approved location when possible.
- If the original location cannot be used, stop with the exact path mismatch
  and treat relocation as a separate reviewed migration. The current builder
  does not claim an automatic relocation has succeeded.

### Corrupt or unavailable brain

- Leave host files intact.
- Report the exact missing or invalid resource.
- Do not substitute inbox, examples, caches, or an old index as canon.
- Restore the complete brain folder, including `.brain-history/`, from a
  private backup, validate it, then rerun connection checks.

### Concurrent host change

- Release locks without writing.
- Preserve the newer user-owned target.
- Require a new preview and exact target approval.

## Security constraints

- Never put credentials, contact details, or private card content in the
  bootstrap.
- Never transmit brain content merely to test connection.
- Use synthetic canaries for host tests.
- Disclose when a host sends opened files to a remote model provider; a local
  installer does not make model processing local.
- Keep generated connection state private and free of secret values.
- Treat a change to the runner contract as a high-impact change: validate all
  hosts after it.
