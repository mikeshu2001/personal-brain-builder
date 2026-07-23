# Private memory runtime

This private copy lets a connected assistant validate, search, save, undo,
and recover memory changes even if the downloaded builder is later
unavailable.

For ordinary work, start with `../runner-contract.md`. Before an explicit
memory change, read `guides/maintenance.md` and `guides/safety-rules.md`.
Run deterministic operations through:

```text
python3 -B system/runtime/scripts/brainctl.py ...
```

This runtime belongs to the private memory. It must not be copied back into
the public builder together with personal state or knowledge.
