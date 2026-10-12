# Task 5 — Static Online Memes and Ephemeral Resource Lifecycle

## Implemented

- Added `OnlineMemeService` for local, auto, and online modes. Local mode never classifies or calls external services; auto returns a local match before authorization; online always uses the approved image-search path and does not claim a local fallback.
- Added a bounded, process-memory-only temporary image store with random 128-bit opaque IDs, group/user/task/source/policy-revision binding, in-flight item/byte reservations, a 20-item and 50 MB ceiling, and a maximum five-minute lifetime. Access, minute cleanup, policy revision checks, revocation, and shutdown clear expired or unauthorized resources. No temporary files or persistent meme-manifest changes are made.
- Added a separate image fetcher with public DNS validation and IP pinning, preserved Host and TLS SNI, standard ports only, no environment proxy or redirect, bounded time/bytes, and format/MIME/pixel checks. Only static PNG, JPEG, and WebP images up to 5 MB and 12 million pixels are accepted.
- Added `/meme --mode local|auto|online`; omitted mode remains local. Online content is revalidated before delivery and sent to OneBot only as validated base64 image bytes. AI `meme_search` uses the exact current user prompt for online lookup and returns an opaque resource handle; the AI chat sender resolves/revalidates that handle against the current user, group, task, and policy before attaching bytes. Local AI meme resource behavior remains unchanged.
- Extended `SearchHit` with an optional image URL populated only from SearXNG image-result `img_src`. The result page URL remains a separate field and is never guessed as the image URL. Page and image hosts each need their own matching approved source rules.

## Rulings and narrow scope extensions

- Search adapters may label a result-page URL and image URL separately. Task 5 therefore consumes SearXNG `img_src` only for image-category results; missing or malformed image URLs are skipped, and no page URL is promoted to an image URL.
- Page results are policy-validated but not fetched. Image fetches reject non-standard ports before source matching and again at the network boundary, even if a source rule was otherwise broad.
- Parent approved two narrow additions to the planned files: `src/aling/services/search.py` preserves the explicit image URL field, and `src/aling/plugins/ai_chat.py` resolves and revalidates online temporary resources before sending. No chat orchestration behavior or panel surface was added.

## Verification

- Focused and compatibility suites: `uv run pytest tests/test_online_memes.py tests/test_memes.py tests/test_meme_writes.py tests/test_search.py tests/test_ai_tools.py -q` → **108 passed**.
- Full suite: `uv run pytest -q` → **526 passed**, with one existing Starlette/httpx deprecation warning.
- `uv run ruff check .` → passed.
- `uv run ruff format --check .` → passed; 100 files formatted.
- `git diff --check` → passed.
- No Docker container, QQ account, live database, or real `.env` was accessed; no deployment or external send was performed.

## Follow-up boundaries

- Task 6 still owns the authenticated panel/API for configuring and diagnosing online search policy.
- Task 7 still owns cross-entry integration/help/privacy regressions; Task 8 owns user-facing operations documentation and release packaging checks.
- Live SearXNG quality, approved image availability, and QQ image delivery remain for owner-controlled field acceptance.
