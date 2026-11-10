# Historical demonstration fixtures

This directory preserves the original demonstration skill payloads, metrics and
lifecycle events. They are sample records, not results of the paper evaluation.
The server starts with a separate `skillwiki-data` directory by default and does
not automatically load these fixtures. Copy this directory to a separate writable
location and select it with `--storage-dir` only when exploring these samples.

Layout: `skills/` contains version payloads; `metadata/` contains indexes and events.
Keep your own runtime store outside this source directory.
