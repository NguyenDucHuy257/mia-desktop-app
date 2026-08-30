# Phase 8 — Portal integration completion

Branch: `feat/offline-phase-08-portal-integration`

## Scope

- Verify credentials directly with the vendored tax-portal session before persisting a new or reconnected account.
- Read the company name from the authenticated company-information endpoint and persist it in local SQLite.
- Never return the portal token, CAPTCHA value or password to the renderer.
- Invoice synchronization forwards the selected date range, purchase/sold directions and overview/detail scopes.
- Each invoice synchronization covers both electronic invoices (`query`) and cash-register invoices (`sco-query`).
- Overview/detail results remain cursor-backed through the local runtime.

## Automated acceptance

- Failed authentication must not create or update an account.
- Successful authentication returns only the company name and immediately displays it in the account table.
- Sync intent tests assert the exact date range, direction, scope, both query types and invoice data type.
- Runtime, IPC and renderer responses must not contain passwords or portal tokens.

## Manual acceptance

Real credentials must be entered only in the packaged UI. Confirm the company name, run a small date range, then compare overview/detail counts with the portal. Do not place credentials in commands, screenshots or logs.

## Local verification adjustments

- Success dialogs use a green check; errors keep the red icon and other notices use the warning icon.
- One shared date-range form is used by Invoice, XML, HTML and PDF. It accepts `dd/mm/yyyy` text or a native calendar selection.
- A new sync after a terminal job creates a new attempt. Double-click/restart while an attempt is active continues to reuse its idempotency key.
- Crawler failures are safely classified as authentication, overview, detail or artifact failures. Logs contain only stage and exception type, never exception messages, tax codes or passwords.
