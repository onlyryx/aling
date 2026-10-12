# Task 4 report — multi-round AI tool flow and truthful capability context

## Delivered

- Added one shared `ModelRequestScheduler` for intent classification, chat, tool-selection rounds, and follow-up responses. It applies the shared concurrency semaphore, reserves each model attempt against the current `TaskBudget`, and clips provider timeouts to the task work deadline.
- Wired the search-purpose classifier through that scheduler. Classification is a single fixed-label request with no history or tools; malformed or uncertain output fails closed through the existing policy gate.
- Extended the AI tool flow to support at most three tool-selection rounds. Per-task limits continue to cap the entire exchange at five model requests and four external operations. Every malformed, unknown, denied, duplicate, or over-limit call receives a protocol `tool` response, including IDs reused in later rounds.
- Connected `web_search` only to the issued current-task `query_id` and existing `SearchService`. Search result reads resolve `result_id` only within that task. Direct URL reads require the exact URL to have appeared in the current user prompt and still pass the existing safe web reader.
- Kept `meme_search`'s local mode compatible. `online` and unavailable `auto` requests fail closed; no Task 5 online media downloader was added.
- Replaced runtime reads of `Settings.ai_tools_enabled` with `FeatureService.ai_tools_enabled()` backed by SQLite. The setting remains only as the existing first-start seed/fallback in `FeatureService`.
- Removed the permanent “no internet” denial from the default persona and injected a per-request capability message based on the tools currently offered. Replies without tools explicitly say no tools are available. Search/read citations and links are checked against actual current-task results before delivery.
- Updated tests for scheduler accounting, classification restrictions, bounded multi-round exchange, duplicate and malformed tool calls, SQLite tool gating, current-task search/read authorization, explicit-URL compatibility, and truthful no-tool prompts.

## Files

- `src/aling/services/ai.py`
- `src/aling/services/tools.py`
- `src/aling/services/search_policy.py`
- `src/aling/plugins/ai_chat.py`
- `src/aling/prompts/default.txt`
- `src/aling/runtime.py`
- `tests/test_ai_tools.py`
- `tests/test_web_plugin.py`
- `tests/test_integration.py`

## Verification

- Focused suites (`test_ai_tools`, `test_sessions`, `test_web_plugin`, `test_features`, `test_integration`) — **45 passed**, existing Starlette/httpx deprecation warning only.
- `uv run pytest -q` — **492 passed**, same existing warning.
- `uv run ruff check src/aling tests` — passed.
- `uv run ruff format --check src/aling tests` — passed; 77 files already formatted.
- `git diff --check` — passed.

## Task boundaries and limitations

- Task 4 is implemented and ready for its independent review. No self-review was performed.
- Task 5 remains responsible for online meme search/download/cache and media-host policy. Task 4 exposes no online media capability; local memes remain available when enabled.
- Task 6 panel integration, Tasks 7/8 cross-entry integration and deployment documentation, and real SearXNG startup/runtime validation were not performed here.
- No real `.env`, database, QQ account, external service, or deployment was accessed.

## Commit

- `feat: support bounded multi-round search tools`
