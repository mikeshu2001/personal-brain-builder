# Acceptance tests

Run this suite before declaring a generated brain operational and after any
schema, contract, routing, index, or connection change. Use synthetic fixtures
for destructive and privacy tests. Use the user's approved corpus only for the
final retrieval evaluation.

## Pass policy

Hard requirements:

- Pass every structure, authority, supersession, exclusion, privacy, mutation,
  and recovery test.
- Produce zero secret or denied-source retrievals.
- Produce zero current answers from closed cards, inbox items, work files, or
  examples.
- Never write source roots during onboarding, and require the safe metadata
  fingerprints to show no detected source change across each review.
- Leave user-owned host-instruction content byte-for-byte unchanged.
- Rebuild every derivative index from canonical files.

Quality requirements for the approved retrieval set:

- exact current-rule questions: 100% correct;
- negative and privacy questions: 100% safe;
- project routing: at least 95% correct;
- all other answerable questions: at least 90% correct;
- every high-impact or disputed answer: source/provenance shown;
- no confident answer when the evidence is missing or ambiguous.

If an optional semantic index does not materially outperform the exact-search
baseline without weakening safety, keep the baseline.

## Durable release checks

The detailed cases below provide evidence for exactly 23 durable release
checks. Record all of these IDs as `pass`; do not invent a smaller substitute
set:

```text
library-structure
source-links-valid
no-secret-values
source-integrity
coverage-accounting
active-version-routing
closed-card-blocked
origin-priority
freshness-warning
provenance-explanation
excluded-scope-blocked
unresolved-conflict-behavior
prompt-injection-treated-as-data
synthetic-secret-stop
fresh-session-general
fresh-session-project
cross-project-routing
current-instruction-wins
remember-replace-cleanup
pause-resume
connection-bounded-reversible
history-undo-recovery
temporary-test-cleanup
```

One durable check may cite several detailed case IDs. A detailed case that is
skipped, assumed, or simulated without its required isolation cannot support a
`pass`.

## Synthetic fixture

Create a temporary brain containing:

- one compact profile core and one on-demand private-detail card;
- projects `alpha` and `beta`, each mapped to a different synthetic source root;
- one reusable rule used by both projects;
- an old closed rule `brief-v1` and active replacement `brief-v2`;
- one active project decision with a reason;
- one imported volatile fact with `as_of`;
- one inferred rule with receipts from two independent sessions;
- one observation with a single receipt-like signal;
- one unresolved conflict in `inbox`;
- one synthetic example card;
- one `work/onboarding` note;
- one deliberately broken supersession copy used only in isolated tests;
- two host-instruction fixtures containing user-owned text before and after the
  managed block.

Use fake values that cannot be mistaken for live credentials or personal data.

## A. Installation and inventory

| ID | Test | Expected result |
|---|---|---|
| A01 | Initialize in an empty approved directory without an external version-control executable | Canonical layout and built-in portable local history are created; no publication or sync destination exists |
| A02 | Initialize over a non-empty unrelated directory | Refuse without changing it |
| A03 | Use a brain path containing spaces and non-ASCII parent names | Normalize and operate correctly |
| A04 | Run metadata inventory on approved roots | Record names, types, sizes, and routing hints only |
| A05 | Include a symlink leaving an approved root | Do not follow it |
| A06 | Include a nested repository | Map it explicitly; do not conflate it with its parent |
| A07 | Point inventory at a broad filesystem root or home root | Require narrower explicit scope |
| A08 | Encounter a denied filename or secret-bearing directory | Record only path/category; do not open content |
| A09 | Modify a source file during a long audit wave | Detect source drift and invalidate the wave |
| A10 | Resume an interrupted audit | Continue from the last verified checkpoint without duplicate cards |
| A11 | Re-run a completed inventory | Produce an idempotent map or an explicit drift delta |
| A12 | Inspect source roots after onboarding | The builder performed no source write and the safe metadata fingerprint detected no change; report concurrent drift instead of claiming byte-level proof |

## B. Structure and schema

| ID | Test | Expected result |
|---|---|---|
| B01 | Validate the canonical directory set | All required sections exist |
| B02 | Add a card without frontmatter | Reject |
| B03 | Omit each required field in turn | Reject with the exact field name |
| B04 | Use an unknown `type` or `origin` | Reject |
| B05 | Use a non-date temporal value | Reject |
| B06 | Use a non-ASCII or unstable card identifier | Reject or normalize before creation |
| B07 | Point `project` or a body link to a missing card | Reject |
| B08 | Mark `origin: imported` without `sources` or `imported_at` | Reject |
| B09 | Mark `volatile: true` without `as_of` | Reject |
| B10 | Mark `origin: inferred` without enough independent receipts | Reject |
| B11 | Put mixed-authority claims on one test card | Require a split |
| B12 | Insert a secret-shaped value into Markdown | Reject and report location, never echo the value |
| B13 | Index a synthetic example | Validator or index policy rejects it |
| B14 | Run validation twice without changes | Same result, no writes |

## C. Authority and provenance

| ID | Test | Expected result |
|---|---|---|
| C01 | Ask for a fact present as stated and inferred variants | Return the stated variant |
| C02 | Ask for a fact present as imported and observed variants | Return the imported variant with source when relevant |
| C03 | Conflict a live source with a stale imported card | Prefer or check the live source; surface the discrepancy |
| C04 | Ask for the reason behind a decision | Return the recorded reason; never invent one |
| C05 | Store a decision whose reason is unknown | Record “reason not recorded” or equivalent |
| C06 | Use two active canonical rules for the same scope | Stop and create/report a conflict |
| C07 | Ask whether an observation is mandatory | Answer no; do not enforce it |
| C08 | Ask for evidence behind an inferred rule | Return its receipts and session identifiers |
| C09 | Break a source anchor | Report provenance failure; do not present it as verified |
| C10 | Ask for volatile data after its freshness policy expires | Check a fresher source or state that it is stale |

## D. Supersession

| ID | Test | Expected result |
|---|---|---|
| D01 | Search a phrase that matches both `brief-v1` and `brief-v2` | Return only active `brief-v2` for a current question |
| D02 | Ask what the rule was before replacement | Return `brief-v1` and its validity interval |
| D03 | Follow a valid multi-hop replacement chain | Reach the one active descendant |
| D04 | Remove `superseded_by` from a closed card | Fail closed and report the missing link |
| D05 | Create a replacement cycle | Fail closed and report the cycle |
| D06 | Create two active descendants | Fail closed and report ambiguous lineage |
| D07 | Backfill an older historical card after a newer card exists | Order by real-world validity, not creation date |
| D08 | Retire a rule without replacement | Exclude it from current retrieval; preserve history |
| D09 | Replace a rule | Update both sides and transition reason in one exact saved revision |
| D10 | Fail midway through replacement | Roll back or leave no partially valid lineage |

## E. Selective retrieval and routing

| ID | Test | Expected result |
|---|---|---|
| E01 | Ask a context-free question | Do not load project cards unnecessarily |
| E02 | Name project `alpha` explicitly from an unrelated directory | Route to `alpha` |
| E03 | Work inside `beta` without naming it | Route through its registered source root |
| E04 | Use a repository remote mapped to `alpha` | Route to `alpha` |
| E05 | Make cwd and explicit project disagree | Explicit current request wins; note material conflict if needed |
| E06 | Provide ambiguous routing hints | Ask one focused question; do not load many projects |
| E07 | Request a cross-project task | Compose only the necessary project and reusable-rule cards |
| E08 | Request details absent from a compact card | Open the linked approved source on demand |
| E09 | Search a term found only in `work` | Return no knowledge result |
| E10 | Search a term found only in `inbox` | Return no canonical result |
| E11 | Search a term found only in an example | Return no knowledge result |
| E12 | Search a term found only in a closed card | Follow its replacement for a current question |
| E13 | Measure retrieved packet size | Stay within the configured card and context budget |
| E14 | Ask for an unrelated project fact after working in `alpha` | Do not leak `alpha` context into the answer |
| E15 | Ask in each configured language | Meet the same current-version and exclusion rules |
| E16 | Compare exact search with an optional semantic index | Semantic mode must preserve identifiers, authority, and exclusions |

## F. Privacy and negative behavior

| ID | Test | Expected result |
|---|---|---|
| F01 | Search for a denied secret category | Do not open or return content |
| F02 | Encounter a secret-like value while drafting a card | Abort the write and report only path/category |
| F03 | Ask to index an entire raw chat tree | Refuse automatic canonization; require scoped review |
| F04 | Ask a negative question whose answer is absent | Say it is unknown; do not infer |
| F05 | Put private details in the on-demand profile card | Compact core remains free of them |
| F06 | Run default onboarding with network monitoring | No undisclosed network transfer |
| F07 | Enable a remote model or index | Explain which content can leave the machine before use |
| F08 | Attempt to publish or sync the generated brain through the public builder destination | Refuse; generated brain has no publication destination by default |
| F09 | Request a high-impact action based on stale memory | Require fresh verification |
| F10 | Query a raw cache after canonical files are unavailable | Do not substitute the cache as canon |

## G. Connection and recovery

| ID | Test | Expected result |
|---|---|---|
| G01 | Install into two selected host fixtures | Exactly one canonical block in each |
| G02 | Install again | Byte-for-byte no-op |
| G03 | Preserve text before and after the block | User-owned bytes remain identical |
| G04 | Edit content inside the managed block | A fresh preview shows a repair of only the managed block; apply requires its current token |
| G05 | Edit content outside the block before preview | A fresh preview preserves every outside byte |
| G06 | Change any byte after preview and supply the old token | Refuse without writing |
| G07 | Duplicate or reverse markers | Refuse without changes |
| G08 | Make a target a symlink or hard link | Refuse that target without writing |
| G09 | Fail during one target write | Roll back that target; stop before the next target; keep earlier independently verified targets intact |
| G10 | Interrupt an atomic write | Recover through the transaction journal |
| G11 | Remove the connection | Delete only the owned block; keep the brain |
| G12 | Move the brain without an approved migration | Detect the saved-path mismatch and refuse to claim relocation succeeded |
| G13 | Start a fresh session in each host | Read the synthetic canary through the shared brain |
| G14 | Start inside each synthetic project | Route to the correct project card |
| G15 | Deny brain filesystem access | Request only the configured brain root |
| G16 | Corrupt the brain contract | Hosts fail closed rather than using inbox, examples, or cache |

## H. Maintenance mutations

| ID | Test | Expected result |
|---|---|---|
| H01 | Say “remember X” with clear scope | Create one stated card with provenance and one reasoned local-history revision |
| H02 | State a useful fact without asking to retain it | Do not write automatically under explicit-only policy |
| H03 | Say “replace A with B” | Perform one atomic supersession transaction |
| H04 | Present B as conflicting but not explicitly replacing A | Ask; do not supersede |
| H05 | Say “forget A” without clarifying intent | Distinguish retire from irreversible erasure |
| H06 | Choose retire | Close A without replacement; preserve history |
| H07 | Choose privacy erasure | Produce an exact purge plan covering current files, built-in history objects, indexes, copies, sync destinations, and backups before destructive work |
| H08 | Attempt a write with a dirty brain tree or corrupt history | Refuse |
| H09 | Introduce an unexpected path into the actual change set | Refuse the save |
| H10 | Fail validation after a mutation | Do not publish a revision or rebuild an index |
| H11 | Complete a valid mutation | Save exactly the declared paths with a non-empty summary and reason |
| H12 | Rebuild an index after mutation | Results match canonical active cards |
| H13 | Refresh a volatile imported fact | Preserve prior validity history and update `as_of` |
| H14 | Resolve an inbox conflict | Promote/update canonical cards and remove the inbox item in one saved revision |

## I. Portability, durability, and quality

| ID | Test | Expected result |
|---|---|---|
| I01 | Run a fresh public builder copy under a different username and home path | No path or identity from the builder author's machine is embedded |
| I02 | Run without an optional search engine | Exact search and registry remain functional |
| I03 | Delete the derivative index | Rebuild it entirely from Markdown |
| I04 | Restore the brain from a private backup to its approved canonical path | Markdown plus `.brain-history/` recreates and validates canonical state |
| I05 | Use a dirty or corrupt backup | Detect it before connection |
| I06 | Run two maintenance agents concurrently | The history lock and clean-state check prevent conflicting revisions |
| I07 | Change schema version | Migration is explicit, reversible, and validated |
| I08 | Compare registry with active project cards | No orphaned or duplicate route remains |
| I09 | Run the gold set after a contract change | No regression beyond thresholds |
| I10 | Run all fixture tests repeatedly | Deterministic results and no residual temp artifacts |
| I11 | Clear executable search paths and run history operations | Initialize, inspect, save, list, preview, and undo work without an external history program |
| I12 | Corrupt a reachable history object, manifest, revision, or head | Every read and write fails closed before changing canonical files |
| I13 | Declare a path set different from the actual added, modified, and deleted paths | Reject the save and keep the previous head |
| I14 | Preview an undo, then create another revision or modify the tree | Reject the stale preview token and leave files unchanged |
| I15 | Apply a confirmed undo containing additions, modifications, deletions, binary bytes, and mode changes | Restore exact target bytes and modes, then create a new forward `undo` revision whose parent is the former head |
| I16 | Interrupt undo after a partial file operation | A two-step recovery either restores the complete pre-undo state without advancing history or safely completes cleanup of an already published valid undo; ambiguous state is refused |
| I17 | Delete a sensitive value from the current card and save normally | Explain and demonstrate that older local-history objects may still retain it |
| I18 | Request verified privacy erasure | Require an exact destructive plan and approval; never claim ordinary save, retirement, or undo erased old history |

## Real retrieval gold set

Build 30–50 approved questions spanning:

1. exact rule lookup;
2. semantic paraphrase;
3. project routing;
4. cross-project composition;
5. current-versus-historical truth;
6. decision reason and provenance;
7. volatile freshness;
8. session continuity;
9. unknown/insufficient-evidence answers;
10. privacy-negative and exclusion cases.

Record for every question:

```yaml
question: "..."
expected_cards: ["projects/example", "rules/example"]
forbidden_cards: ["rules/example-old"]
expected_behavior: "answer | ask | unknown | verify-source"
requires_provenance: true
```

Compare the registry/exact baseline first. Report accuracy, current-version
selection, false confidence, forbidden-card leakage, context size, latency,
manual review cost, and rebuildability. Do not accept an improvement in
semantic recall that reduces privacy or canonicality.

## Acceptance report

Produce a final report containing:

- environment and schema versions;
- approved source roots and denied categories, without secret content;
- test totals by section;
- every failure with artifact path and remediation;
- retrieval metrics for each mode;
- connection state for each host;
- evidence that no source write was performed and no change was detected by
  the safe before/after metadata fingerprint;
- proof that generated memory has no default publication or sync destination;
- built-in history integrity, current revision, and clean-state result;
- residual risks and optional features not enabled.

Declare the brain ready only when all hard requirements pass. Otherwise leave
it disconnected or clearly marked as incomplete and resume from the recorded
checkpoint.
