Captured from Unstop's public API on 2026-11-05:

- `offline.json`: `https://unstop.com/api/public/competition/1654684`
- `online.json`: `https://unstop.com/api/public/competition/1563941`

Objects are taken from `data.competition`, narrowed to adapter fields. Description
HTML is a short source excerpt; registration contacts and unrelated metadata are
excluded. The public search listing omitted `start_date`, requiring the detail
fallback. Tests build synthetic pagination envelopes around these real records.
