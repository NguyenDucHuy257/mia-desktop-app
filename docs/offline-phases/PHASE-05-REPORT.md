# Phase 5 — Local artifacts and Excel

## Implemented

- Folder chooser is owned by Electron main and exposed through one allowlisted IPC method.
- Export requests are sanitized again in main: absolute destination, 1–50 unique account IDs and allowlisted kinds only.
- XML/HTML/PDF files are copied only from crawler job export directories.
- Excel overview export supports Vietnamese Unicode and neutralizes formula prefixes.
- Writes use a temporary file plus atomic rename; incomplete temporary files are removed.
- Duplicate names receive a numbered suffix; traversal, Windows reserved names and invalid extensions are rejected.
- XML, HTML and PDF screens call the local artifact runtime and preserve their Figma baselines.
- XML/HTML/PDF package download is invoked from the vendored crawler after overview/detail synchronization.
- Artifact tables list runtime files, combine account/date/direction/search filters and retain opaque cursor history.
- Chromium is installed into a dedicated runtime resource directory and shipped with the application; it is never downloaded on first use.

## Verification gate

- Unit: path traversal, reserved names, duplicate files, atomic cleanup and DTO sanitization.
- Python: Excel content, Unicode, formula injection, duplicate suffix and invalid selections.
- Interaction/visual: all artifact filters, pagination, progress and cancel states.
- Packaged-runtime smoke: PASS, including launching and closing bundled Chromium from `mia-runtime.exe`.
- Real portal artifact integrity remains NOT RUN until a packaged-UI crawl is performed.
