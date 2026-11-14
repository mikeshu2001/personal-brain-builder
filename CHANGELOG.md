# Changelog

Releases of the builder. A finished memory keeps its own copy of the
operating files and changes only when its owner runs the upgrade described in
the README.

## 1.2.1 — 2026-11-14

### Fixed

- **A new memory in a folder that Finder already touched.** A target folder
  that holds only `.DS_Store`, `Thumbs.db`, or `desktop.ini` now counts as
  empty for `doctor` and `init`. Before, creating the folder in Finder first
  could make memory creation refuse it as non-empty. The file stays in the
  folder, also when creation fails; a folder with any other content is still
  refused.
- **Secret screening of notes and reasons.** Text stored in setup state
  (approval notes, consent notes, next actions, reasons, evidence) is now
  screened as written. Before, a value in double quotes or on the next line,
  such as `api_key="…"`, passed the screen everywhere except the upgrade
  approval note.
- **Undo of an upgrade keeps reviewed work.** An undo that restores only
  operating files under `system/` no longer marks reviewed work areas and
  conversation sources for a new review. The memory still fails closed, and
  acceptance checks and connections are verified again. An undo that also
  restores knowledge behaves as before, and so does an undo finished through
  history recovery or reconciliation.

### Upgrade notes

- An existing memory does not need this release to keep working. Upgrade it
  when you want the stricter screening of notes or the lighter undo.

## 1.2.0 — 2026-11-12

### Fixed

- **File-manager files no longer block a memory.** `.DS_Store`, `Thumbs.db`,
  and `desktop.ini` are skipped by the history scan and by validation.
  Before, opening the memory folder in Finder could leave a `.DS_Store` that
  counted as an unsaved change, and search and saves refused until someone
  deleted it. The same files inside the builder's template folder no longer
  stop the creation of a new memory.
- **Removing a connection whose block is already gone.** `connect remove`
  now clears the connection record when the managed block was deleted from
  the instruction file. Before, the record stayed and the memory kept
  failing validation with nothing left to remove.

### Added

- **Another assistant for a finished memory.** A ready memory can reopen
  connection review on the owner's request. The new connection goes through
  the usual preview, approval, and fresh-conversation verification, and the
  memory returns to the ready state through acceptance, the recovery-copy
  review, and handoff. Recorded acceptance results stay valid. The same path
  repairs a connection whose instruction file was edited after connection:
  remove it, reopen connection review, connect again.

### Changed

- The owner dialogue no longer offers to add a work folder to a finished
  memory. The control tool has no supported path for that yet.

### Upgrade notes

- An existing memory gets both changes through the upgrade from this
  builder. A memory that already holds a stray `.DS_Store` can be upgraded
  without deleting it.

## 1.1.0 — 2026-11-12

### Added

- **Upgrade for an existing memory.** `brainctl.py upgrade preview` and
  `upgrade apply` bring the operating files of an already built memory (runner
  contract, connection text, guides, templates, control scripts) to the
  version of the downloaded builder. The preview writes nothing. The apply
  step needs the current token and an approval note, saves one local-history
  revision, and returns the memory to its recheck point, so acceptance checks
  and assistant connections are verified again. It refuses a memory that
  fails validation, unsaved memory changes, a recorded blocking issue, an
  unfinished resume, and a builder older than the memory. After a run
  interrupted between files, a new preview finishes the same upgrade.
- **Delegated workers skip the memory.** The connection block and the runner
  contract now say that a subagent or headless worker on a narrow delegated
  brief does not read the memory unless the brief names cards; the delegating
  agent states that the brief is narrow and passes the context. Hosts load
  their global instructions into spawned workers too, so before this change
  each worker repeated the start-up reads. In one measured setup scripted
  workers read those files on every run and opened no card, and the same
  worker task used about 40% fewer input tokens with the exception in place.
- **Finished one-off work stays names-only by default.** Before proposing
  content depth the assistant asks which work areas the owner still runs and
  proposes names-only for finished one-off work. The owner decides.
- **Notice about an assistant's own memory.** During connection the owner is
  told that a host's own memory feature keeps working separately and is not
  shared with other assistants.
- Acceptance row `E17` for delegated workers.
- `BUILDER_VERSION` in the control tool, so an upgrade can compare a memory
  with the downloaded builder.

### Upgrade notes

- A memory built with 1.0.0 keeps working without any action. It keeps its
  original contract and connection text until its owner asks an assistant to
  run the upgrade from a current builder.
- After an upgrade, a connection preview shows the new connection text for
  each connected assistant. It is applied only after a separate approval.
- A memory must pass validation before an upgrade. If an assistant
  instruction file was edited after connection, remove that connection first
  and connect the assistant again during the recheck.
- The previous operating files stay in local history, but an undo marks
  every reviewed work area for a new review, so prefer a newer builder over
  undoing an upgrade.

## 1.0.0 — 2026-07-23

- Initial public release: guided build of a private cross-project memory,
  deterministic control tool, bounded reversible assistant connections, and
  built-in local history.
