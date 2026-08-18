# Phase 6.5 — UI/UX hardening

## Implemented

- Three-dot account menu supports viewing results, exporting one account and exporting all selected accounts.
- Popovers close on outside click and Escape; batch selection remains independent and indeterminate-aware.
- One shared output folder is used by invoice, XML, HTML and PDF flows.
- Results search is debounced, has quick clear and ignores stale responses through generation tokens.
- Notice/error dialogs close with Escape, trap keyboard focus and restore focus to their trigger.
- Materials, logs and settings screens now expose working search/configuration controls instead of release placeholders.
- Existing Figma default frames remain unchanged and continue using direct Figma PNG baselines.
- Production account and artifact screens no longer fall back to synthetic customer/file rows when runtime calls fail; fixtures require an explicit test query flag.
- Artifact search and opaque next/previous cursor navigation are backed by the local runtime and discard stale responses.

## Automated gates

- Unit/runtime tests cover lifecycle, cursor datasets, filesystem safety, Excel integrity and batch limits.
- Playwright covers account lifecycle, job create/poll/cancel/retry, result cursor flow, menu keyboard behavior, export actions, artifact controls and responsive widths.
- Current automated totals: Python 18/18, Vitest 81/81 and Playwright 23/23 PASS.
- Renderer secret scan and offline source-manifest verification remain mandatory.

## Remaining acceptance gate

- Windows packaged manual flow with real portal credentials: NOT RUN.
- Activation remains SKIPPED by explicit project decision.
