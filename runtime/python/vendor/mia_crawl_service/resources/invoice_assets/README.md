# Invoice HTML assets

Optional fixed portal assets belong in this directory:

- `sign-check.jpg`
- `viewinvoice-bg.jpg`
- `details.js` (only when the downloaded HTML references it)

The package storage service prefers files placed here. If a fixed asset is not
present, it uses the matching file from the downloaded ZIP and logs a warning
when neither source contains it. Real portal assets are intentionally not
committed as fabricated placeholders.
