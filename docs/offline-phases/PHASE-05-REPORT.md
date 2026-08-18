# Phase 5 — Local artifacts and Excel

## Implemented

- Folder chooser is owned by Electron main and exposed through one allowlisted IPC method.
- Export requests are sanitized again in main: absolute destination, 1–50 unique account IDs and allowlisted kinds only.
- XML/HTML/PDF files are copied only from crawler job export directories.
- Excel overview export supports Vietnamese Unicode and neutralizes formula prefixes.
- Writes use a temporary file plus atomic rename; incomplete temporary files are removed.
- Duplicate names receive a numbered suffix; traversal, Windows reserved names and invalid extensions are rejected.
- XML, HTML and PDF screens call the local artifact runtime and preserve their Figma baselines.

## Verification gate

- Unit: path traversal, reserved names, duplicate files, atomic cleanup and DTO sanitization.
- Python: Excel content, Unicode, formula injection, duplicate suffix and invalid selections.
- Interaction/visual: all artifact filters, pagination, progress and cancel states.
- Real portal artifact integrity remains NOT RUN until a packaged-UI crawl is performed.
