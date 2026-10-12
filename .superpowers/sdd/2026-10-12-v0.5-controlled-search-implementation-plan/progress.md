# SDD ledger — plan: docs/superpowers/plans/2026-10-12-v0.5-controlled-search-implementation-plan.md

- Worktree: `/Users/jack233/.codex/worktrees/aling-v03-upgrade/ryxbot`
- Branch: `codex/aling-v05-design`
- Starting BASE: `3db7780aca79ddfbb6828513c303727f360340a8`
- Worktree was clean before implementation. It is a linked worktree; the main checkout and live bot are outside this task.
- Baseline: `uv run --frozen --extra dev pytest -q` → 309 passed, 1 existing Starlette/httpx deprecation warning, 7.49s.
- Plan: `docs/superpowers/plans/2026-10-12-v0.5-controlled-search-implementation-plan.md`; binding spec: `docs/superpowers/specs/2026-10-12-v0.5-controlled-search-design.md`.

## Preflight conflict/interface scan

| Pair / task | Shared file or interface | Finding |
|---|---|---|
| Task 1 → Task 2 | SQLite search-policy records and feature state | Task 1 produces persisted policy and feature APIs; Task 2 consumes them. Sequential ordering is sufficient; no schema owner conflict. |
| Task 1 → Task 7 | commands/help/feature behavior | Task 7 later integrates and regression-checks Task 1 command/help surfaces; no competing implementation owner. |
| Task 2 → Task 3 | SearchPolicyService, classifier Protocol, task context | Task 3 SearchService consumes the gate from Task 2; classifier implementation arrives in Task 4. Protocol intentionally allows this staged wiring. |
| Task 2 → Task 4 | SearchTaskContext and lazy classifier contract | Task 4 supplies scheduler-backed classifier and AI tool use. Context must be resolved through runtime registry, not trusted from model arguments; plan explicitly requires this. |
| Task 2 → Task 5 | source/result authorization, policy revision | Online memes consume the same purpose/source gate; source and media host must both pass validation. No alternate authorization path planned. |
| Task 2 → Task 6 | persisted owner/group policy API | Panel routes consume Task 1/2 stores/services, while group narrowing remains within owner maxima. |
| Task 3 → Task 4 | TaskBudget, SearchService, task model accounting | Task 4 adds model scheduling on the budget from Task 3. Model and network semaphores remain distinct as required. |
| Task 3 → Task 5 | SearchService, TaskBudget, SearXNG results | OnlineMemeService consumes the bounded search client and must add its one download within the same task budget. |
| Task 3 → Task 6 | fixed search client/status/probe | Panel probe uses the same SearchService and no separate unrestricted client. |
| Task 3 → Task 7 | `/search` handler and service | Task 7 checks unified gates and user-visible errors for the new command. |
| Task 3 → Task 8 | Compose profile, settings, status behavior | Task 8 documents and verifies the opt-in deployment produced by Task 3. |
| Task 4 → Task 5 | `services/tools.py`, `runtime.py` | Task 5 extends mode-aware meme execution in shared files after Task 4; strictly sequential, reviewer gate between. |
| Task 4 → Task 7 | AI chat path, tools, runtime | Task 7 owns cross-entry integration/regression only after Task 4 implementation. |
| Task 5 → Task 6 | online meme mode, cache/revoke runtime interfaces | Panel displays/configures modes and policy, relying on Task 5's runtime services; no second cache implementation. |
| Task 5 → Task 7 | meme plugin, runtime and local compatibility | Task 7 validates local/online behavior and send revocation after Task 5. |
| Task 6 → Task 7 | panel status/policy behavior | Task 7 checks QQ and panel routes use the same gates, including probe denial. |
| Task 1 | Own text/tests against code | Schema v4 rebuilds only the two feature tables whose CHECK constraints exclude new IDs; preserve copied rows transactionally. Policy seed, owner consent, AI-tools seed/accessors and global/group commands all have explicit tests/steps. |
| Task 2 | Own text/tests against code | Trusted context factory, registry resolution, conservative classifier, source matching, and explicit `/read` compatibility are all covered by its service/tests. |
| Task 3 | Own text/tests against code | Budget, fixed-origin POST client, search plugin, optional Compose service, log scrub, and final three-second delivery contract have explicit owners/tests. |
| Task 4 | Own text/tests against code | Scheduler, multi-round protocol, per-request capability text and SQLite AI-tools caller wiring are covered in service/plugin tests. |
| Task 5 | Own text/tests against code | Local/auto/online behavior, SSRF-safe image fetch, in-memory reservation/expiry/revoke and send-time validation have explicit interfaces/tests. |
| Task 6 | Own text/tests against code | Strict owner APIs, consent, runtime policy cancellation, AI-tools UI, status diagnostics and static UI distribution have explicit tests. |
| Task 7 | Own text/tests against code | Cross-entry fake-runtime tests plus named v0.4 regression areas and privacy capture are explicit. |
| Task 8 | Own text/tests against code | Packaging version 0.5.0, docs, Compose merge, full CI checks, whole-branch review, and stop before external release actions are explicit. |

Preflight result: no conflict requiring a design change. The spec is authoritative; any implementation ambiguity is resolved against it and recorded as a `Ruling:` before proceeding. Baseline warning is existing dependency deprecation only; do not expand scope unless a v0.5 check exposes a blocker.

## Rulings

- Task 5: SearXNG image-category `img_src` is the only accepted candidate image URL; the ordinary result `url` stays a separately validated source page. Missing image URLs are skipped instead of inferred. Both sources require their own explicit matching rules, and result-page/image port guards reject non-standard ports before network activity.
- Task 5 scope extension: parent approved adding optional `SearchHit.image_url` parsing in `services/search.py` and a send-time opaque-resource resolution check in `plugins/ai_chat.py`. The latter does not change AI orchestration.

## Completed task gates

- Task 1 implementer: `v05_task1_impl`; commit `4fa3c12b7a12de917770d09b89a44065cbbfb9ae` (`feat: add persisted search policy gates`). Focused tests 59 passed; full pytest 326 passed; Ruff and diff checks passed.
- Task 1 reviewer: `v05_task1_review`; approved, no critical or important findings. Minor note: existing Starlette/httpx deprecation warning. Review report: `task-1-review.md`.
- Task 1 complete (commits `3db7780..4fa3c12`, required focused command → 59 passed; reviewer approved).
- Task 2 implementer: `v05_task2_impl`; commit `82b4815dc3530b01f58c6607605f0898f1b4cd7f` (`feat: add shared search policy gate`). Required focused command → 133 passed; full suite → 455 passed, one existing deprecation warning; Ruff, formatting, diff checks passed. Report: `task-2-report.md`.
- Task 2 reviewer: `v05_task2_review`; independent review found one important issue: result URLs were not bound to each source's scheme.
- Task 2 follow-up fix: `a128234` (`fix: bind search sources to schemes`); sources default to HTTPS (including legacy JSON), explicit HTTP remains configurable, and URL validation enforces the configured scheme. Focused storage/search/plugin tests → 148 passed; full suite → 456 passed with the existing Starlette/httpx warning; Ruff and formatting passed.
- Task 2 fix reviewer: `v05_scheme_fix_review`; approved with no findings after tracing storage, validation, and result resolution paths. Task 2 gate is complete.
- Task 3 implementer: `v05_task3_impl`; commit `d0707fd` (`feat: add bounded SearXNG search`). Full suite → 483 passed; Ruff, format, Compose merge validation, and diff checks passed. SearXNG container startup/log behavior remains unverified because no container was started. Report: `task-3-report.md`.
- Task 3 reviewer: `v05_task3_review`; found cooldown pruning used the instance default rather than per-policy duration. Fixed in `5abc185` (`fix: honor configured search cooldowns`) with a regression test; full suite → 484 passed, existing warning only; Ruff/format/diff checks passed.
- Task 3 fix reviewer: `v05_cooldown_fix_review`; approved with no findings. Task 3 gate is complete.
- Task 4 implementer: `v05_task4_impl`; commit `18525a4` (`feat: support bounded multi-round search tools`). Full suite → 492 passed; focused suites → 45 passed; Ruff, format, and diff checks passed. Report: `task-4-report.md`.
- Task 4 reviewer: `v05_task4_review`; found (1) stale initial context prevented search result reading and (2) non-function tool call types were executable. Fixed in `1b3c93f` (`fix: preserve authorized search task context`) with real policy/ToolRegistry flow and malformed-type tests. Focused + policy suites → 176 passed; full suite → 494 passed; Ruff/format/diff checks passed.
- Task 4 fix reviewer: `v05_task4_fix_review`; approved with no findings; focused `test_ai_tools` + `test_search_policy` → 151 passed. Task 4 gate is complete.
- Task 5 implementer: `v05_task5_impl`; commits `b55ffe6` (`feat: add safe online meme lookup`) and `94d2e88` (`fix: preserve online meme quotas and revocation`). Focused + compatibility suites → 124 passed; full suite → 530 passed with the existing Starlette/httpx deprecation warning; Ruff check, Ruff format, and `git diff --check` passed. Fresh review found two P2 issues (admission lifetime and feature off→on revocation), both fixed; follow-up identified a send-time interleaving and approved the final post-await generation check. Report: `task-5-report.md`. Task 6+ remain unstarted.

- Task 6 implementer: `v05_task6_impl`; owner/group policy, consent, persisted AI-tools, probe/status APIs, and Blue WebUI implemented. Plan focused WebUI command → 23 passed; expanded Task 6 WebUI/AI-tools set → 33 passed; connected feature/search-policy/online-meme suites → 168 passed. Ruff check/format, JS syntax, and diff checks passed. Report: `task-6-report.md`. **Ruling:** added configured-group GET/PUT policy routes because the required UI needs group-level narrowing and the plan does not name those route paths. Task 6 commit `fbb63b6` independently reviewed and approved.

- Task 7 implementer: `v05_task7_impl`; added dynamic `/help` availability/mode guidance and cross-entry fake-runtime regressions for disabled QQ, AI-tool, and owner-probe gates, lazy classification, and log privacy. Exact Task 7 suite → 234 passed; scheduler regression suite → 9 passed; Ruff check/format and diff checks passed. Report: `task-7-report.md`. Awaiting independent review; Task 8 remains unstarted.

- Task 8 implementer: `v05_task8_impl`; updated Chinese-first operations/deployment docs, `.env.example`, package/CLI version and resource checks, release changelog, and verification evidence. Full suite → 544 passed; focused CLI/distribution → 9 passed; Compose test → 2 passed; Ruff, format, lock, package build, both base/search Compose checks and local Docker image build passed. Compose review found and fixed shared-env leakage of `SEARXNG_SECRET` into bot; regression covers bot suppression/search-only delivery. No SearXNG, bot, NapCat, QQ, or other external service was started/used. Report: `task-8-report.md`. Task 8 implementation commit is `chore: prepare ALING v0.5 release`; stop for independent whole-branch review.

- Task 3 implementation: `v05_task3_impl`; focused/full suite → 483 passed, one existing Starlette/httpx warning; Ruff, formatting, Compose config and diff checks passed. Added bounded budgets/admission, fixed-origin SearXNG POST client, `/search`, optional Compose profile and integration coverage. Report: `task-3-report.md`. Parent-approved narrow Runtime ownership extension is recorded in the report. Actual SearXNG startup/container query-log behavior remains unverified because no SearXNG container was started; reviewer should account for this limitation.
- Task 4 implementation: `v05_task4_impl`; focused suite → 45 passed; full suite → 492 passed with the existing Starlette/httpx deprecation warning; Ruff, formatting, and diff checks passed. Added shared scheduler-backed model request accounting, bounded multi-round tool protocol, task-context-bound search/read operations, SQLite AI-tools state, and dynamic capability context. Report: `task-4-report.md`. Awaiting independent review; Tasks 5+ remain unstarted.
