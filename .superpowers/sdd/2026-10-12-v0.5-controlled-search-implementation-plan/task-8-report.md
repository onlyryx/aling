# Task 8 — Deployment documentation, packaging, and release acceptance

## Delivered

- Added Chinese-first [search operations guidance](../../../../docs/search-operations.md) covering optional SearXNG setup, the pinned image tag, enabled engines and caveats, JSON/SafeSearch behavior, source review, group/deployment policies, classifier and model data flow/cost, local/auto/online meme modes, status troubleshooting, logging, backup, rollback, and unverified field acceptance.
- Updated README, architecture, verification record, and changelog for v0.5 behavior and `0.5.0` release metadata.
- Added only safe placeholders and bounded SearXNG origin/client settings to `.env.example`; no real `.env` or database was accessed. Ignored the generated `deploy/searxng/settings.yml` file so its secret is not accidentally committed.
- Set package/CLI version to `0.5.0`, updated the lock and CLI/metadata assertions, and extended wheel distribution coverage to the bundled default prompt.
- Prevented the shared Compose env file from passing `SEARXNG_SECRET` into the bot container; a test asserts that the bot gets an empty value while the SearXNG service gets the configured interpolation. Existing project name and named volumes remain unchanged; search has no published port.

## Verification

- `uv run --frozen --extra dev pytest tests/test_cli.py tests/webui/test_distribution.py -q` — **9 passed**, existing Starlette/httpx deprecation warning.
- `uv run --frozen --extra dev pytest -q` — **544 passed**, same existing warning. One stale `test_rename.py` version assertion was updated from 0.4.0 to 0.5.0 before the final full run.
- `uv run --frozen --extra dev pytest tests/test_compose.py -q` — **2 passed** after secret-isolation assertions were added.
- `uv run --frozen --extra dev ruff check .`, `uv run --frozen --extra dev ruff format --check .`, `uv lock --check` — passed.
- `uv build` — produced `aling-0.5.0.tar.gz` and `aling-0.5.0-py3-none-any.whl`; `uv run --frozen aling --version` — `ALING 0.5.0`.
- Both required Compose `config --quiet` commands passed using `.env.example`. Parsed base and merged configs confirmed project `ryxbot`, legacy `bot-data`/`napcat-qq`/`napcat-config` volumes, no SearXNG host port, shared bot/search network, and secret only in the search service.
- Docker Engine was available; `docker build -t aling:0.5.0-task8-check .` passed. No container or external service was started.
- `git diff --check` passed.

## Boundaries and review handoff

- The SearXNG container and its runtime/access logs were not exercised. Live engine availability, search quality, model provider handling/cost, real image availability, and QQ delivery remain owner-controlled field checks.
- No push, tag, deploy, NapCat login, QQ message, real `.env`, live database, or account was touched.
- Task 8 implementation is committed; the requested fresh whole-branch read-only review is the next gate.

## Commit

- `chore: prepare ALING v0.5 release`

## Whole-branch review follow-up

- The independent whole-branch review identified unsafe wrapped OAuth callback queries reaching classification, `/search` task registration before the input cap and retention of the event object, a tool-enabled capability retry after provider HTTP rejection, and ignored persisted group meme mode. Wrapped and percent-encoded callback URLs/secrets now fail preflight before classifier or SearXNG calls. `/search` rejects queries over 100 characters before task registration; task records keep only the bounded query and immutable principal snapshot. A first-round model HTTP status error now returns a fixed message stating that no online operation ran. Omitted `/meme` mode uses the stored group mode; explicit modes bypass that lookup, and a failed policy read returns a clear unavailable message without searching.
- Added regression tests for classifier/search suppression, task size/retention/principal binding, provider rejection, and stored `auto`/`online` meme defaults with explicit override.
- Final affected suite: `uv run --frozen --extra dev pytest tests/test_search_policy.py tests/test_search.py tests/test_ai_tools.py tests/test_memes.py -q` — **202 passed**.
- Final full suite: `uv run --frozen --extra dev pytest -q` — **561 passed**, existing Starlette/httpx deprecation warning.
- Final Ruff check, format check, and `git diff --check` passed. No live services, real `.env`, account, or database were used.
- Review-fix commit is separate from the Task 8 implementation commit; awaiting a fresh read-only re-review.

### Nested-encoding follow-up

- A fresh re-review found that one percent-decoding pass still allowed doubly encoded callback URLs and `code`/`state` secrets. Added two end-to-end regressions; before the fix each reached the classifier, while the search client remained uncalled.
- `_unsafe_query` now checks every decoded form until stable, bounded by the 100-character input cap, and fails closed if decoding does not stabilize within that bound.
- A subsequent security check identified percent-encoded generic credentials (`token=`, `api_key=`, `password=`). Added end-to-end tests; prior to the fix, token/password cases reached the classifier.
- Every decode layer now runs the existing sensitive-value, forbidden-term, contact, query-operator, callback and URL checks. Decoded controls and separators are rejected too; decoding remains bounded by the 100-character query limit.
- Focused search and policy tests passed (**141 passed**); the final affected suite passed (**202 passed**) and the full suite passed (**561 passed**). Ruff check/format and `git diff --check` passed.
- Follow-up commits are `1029e20`, `89ba89d`, and `6ae07a1`. A fresh independent re-review of the final fix set approved it with no findings; the targeted suite passed **202 tests**.

## Final acceptance audit — HEAD `6ae07a1`

- Fresh full-suite run: `uv run --frozen --extra dev pytest -q` — **561 passed**, one existing Starlette/httpx deprecation warning.
- `uv run --frozen --extra dev ruff check .`, `uv run --frozen --extra dev ruff format --check .`, `uv lock --check`, and `git diff --check HEAD` — passed.
- Rebuilt source distribution and wheel with `uv build`; `uv run --frozen aling --version` reports `ALING 0.5.0`.
- Both base and optional-search Compose `config --quiet` checks passed with `.env.example`.
- Rebuilt the Docker image from final source successfully. No container or external service was started.
- Final worktree is clean. No push, tag, deployment, QQ/NapCat login, real `.env`, or live database access occurred.
