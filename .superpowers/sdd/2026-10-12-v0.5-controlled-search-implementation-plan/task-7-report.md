# Task 7 — Entry-Point Integration, Help, and Privacy Regressions

## Implemented

- `/help` now states that `/meme` defaults to local assets, shows whether search and online memes are enabled for the current scope, and notes that an enabled online feature still depends on service readiness and approved sources.
- Expanded the fake OneBot/WebUI integration test to exercise disabled QQ `/search`, `/meme --mode online`, AI `web_search` and `meme_search`, and owner probe paths against the same runtime. The test asserts zero classifier, search, and image-download calls while the online features are unavailable.
- Added call-count assertions showing local `/meme` and ordinary AI chat do not invoke the purpose classifier.
- Added log-capture sentinels for the fake query, result URL credentials/token, unapproved result body, and local image path.
- The existing integration coverage continues to exercise `/help`, `/ping`, `/proactive`, memory, moderation permission isolation, sessions, local memes, WebSocket authentication/setup, and persisted AI-tools behavior. Existing dedicated tests cover explicit `/read` compatibility, panel authorization, policy revocation, and scheduled messages.

## TDD and verification

- Added the help regression first; its initial run failed because the requested local-only default and current online feature status were absent.
- `uv run pytest tests/test_search.py tests/test_ai_tools.py tests/test_integration.py tests/test_help.py tests/test_services.py tests/test_web_plugin.py tests/test_memes.py tests/test_features.py tests/webui -q` — **234 passed**, one existing Starlette/httpx deprecation warning.
- `uv run pytest tests/test_scheduler.py -q` — **9 passed**.
- `uv run ruff check src/aling tests` — passed.
- `uv run ruff format --check src/aling tests` — passed; 82 files already formatted.
- `git diff --check` — passed.

## Boundaries

- No direct `/read` policy, unrelated panel/API behavior, deployment, push, live environment/database, QQ account, or real external service was changed or used.
- Task 7 is ready for independent review. Task 8 remains unstarted.
