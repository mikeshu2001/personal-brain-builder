# Security

## Reporting a problem

Report security issues privately to the repository owner rather than opening
a public issue containing sensitive paths, personal memory, credentials, or
source excerpts.

## Safety boundaries

- Source folders are read-only during the initial build.
- Folder names are mapped before file contents are considered.
- Content, past assistant conversations, connection changes, backup,
  publication, and deletion each require separate approval.
- Symbolic links are not followed during recursive discovery.
- Known credential stores and secret-shaped values are blocked.
- Connection changes use a bounded, previewed block and a current
  confirmation token.
- The generated memory has local reversible history but no public destination
  is configured.
- The generated memory root and private operating files reject access modes
  that expose them to another local account.
- A finished memory carries its own maintenance and recovery runtime under
  `system/runtime/`.

Use only synthetic data in bug reports and tests.
