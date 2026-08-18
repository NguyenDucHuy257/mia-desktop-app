# Phase 7 — Release hardening report

## Implemented

- NSIS x64 remains explicit; packaged app includes the one-folder Python/crawler runtime.
- GitHub stable/beta update channels use explicit check/download/install states; auto-download is disabled.
- Install happens only after a complete download and is delegated to electron-updater's atomic installer path.
- Update errors are sanitized before reaching renderer state.
- Package scan rejects embedded GitHub tokens, private keys and assigned service API keys in `app.asar` and runtime files.
- CI uploads installer, update metadata and blockmap while retaining the unsigned artifact label.
- Packaging never publishes (`--publish never`); release publication requires a separately authorized token workflow.

## Gates

- `BLOCKED: code-signing certificate` — no managed Windows signing certificate is available in this environment.
- Signed installer verification: NOT RUN.
- Clean Windows install/update/network-loss/rollback/uninstall matrix: NOT RUN.
- Unsigned installer is a test artifact only and must not be called production.
- Local NSIS retry: FAIL (`EPERM` while electron-builder renamed a freshly extracted `win-unpacked.tmp` directory); runtime build and packaged-runtime smoke passed. CI Windows packaging is the authoritative retry because earlier stacked phase jobs package successfully there.
