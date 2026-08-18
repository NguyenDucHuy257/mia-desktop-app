# Phase 4B — Multi-account local orchestration

## Implemented

- Independent account selection, visible select-all and indeterminate state.
- Search/filter-safe account mapping; row actions always target the displayed account.
- One idempotent child job per account with a bounded concurrency of two.
- Per-account progress, batch cancel, bounded polling retry and stale timer cleanup.
- Resume of all non-terminal child jobs through the IPC allowlist.
- Maximum batch size of 50 and duplicate-account elimination.

## Security boundary

- Credentials remain encrypted at rest and are decrypted only in Electron main.
- No credential, tax code, request body or runtime smoke data is committed.
- Renderer receives job/result DTOs only.

## Verification

- Automated unit, Python storage, renderer boundary, interaction and visual tests are required before the phase PR is opened.
- Real portal smoke remains a separate manual gate using credentials entered through the packaged UI.
