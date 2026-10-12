import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

from aling.core.config import Settings
from aling.core.storage import (
    DeploymentSearchPolicy,
    GroupSearchPolicy,
    GroupSettingsStore,
    SearchSourceRule,
)
from aling.services.features import FeatureService
from aling.services.search_policy import SearchPolicyDenied, SearchPolicyService, SearchPurpose
from aling.webui.auth import PanelAuth


def event(group_id=34, user_id=12, text="Python docs"):
    return GroupMessageEvent(
        time=0,
        self_id=99,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        user_id=user_id,
        group_id=group_id,
        message=Message(text),
        raw_message=text,
        font=0,
        sender={"user_id": user_id, "role": "member"},
    )


class Classifier:
    def __init__(self, purpose=SearchPurpose.TECHNICAL):
        self.purpose, self.calls = purpose, []

    async def classify(self, text, *, budget):
        self.calls.append((text, budget))
        if isinstance(self.purpose, Exception):
            raise self.purpose
        return self.purpose


@pytest.fixture
async def gate(tmp_path):
    settings = Settings(
        _env_file=None,
        onebot_access_token="fictional",
        ai_api_key="fictional",
        ai_model="test",
        superusers={1},
        allowed_groups={34, 35},
        webui_owner_id=1,
        webui_admin_token="fictional-panel-" + "a" * 40,
    )
    store = GroupSettingsStore(tmp_path / "settings.db", superusers={1})
    await store.initialize()
    await store.set_deployment_search_policy(
        DeploymentSearchPolicy(
            sources=(
                SearchSourceRule(
                    "python",
                    "technical",
                    "docs.python.org",
                    "/3",
                    allowed_query_params=frozenset({"id", "lang"}),
                ),
                SearchSourceRule(
                    "meme", "ordinary_meme", "media.example.org", "/approved", kind="image"
                ),
                SearchSourceRule("idna", "technical", "例子.中国", "/docs"),
            )
        )
    )
    await store.set_search_consent(1, True)
    features = FeatureService(settings, store)
    await features.initialize()
    for feature in ("ai", "web", "meme", "search", "online_meme"):
        await features.set_global(1, feature, True)
        for group_id in (34, 35):
            await store.set_feature_enabled(group_id, feature, True)
    classifier = Classifier()
    now = [100.0]
    auth = PanelAuth(settings, clock=lambda: now[0])
    service = SearchPolicyService(
        features, classifier=classifier, panel_auth=auth, clock=lambda: now[0]
    )
    return SimpleNamespace(
        service=service,
        store=store,
        features=features,
        classifier=classifier,
        now=now,
        auth=auth,
        settings=settings,
    )


async def test_technical_normalization_frozen_query_sources_and_one_search(gate):
    context = await gate.service.create_task_from_event(event(text="Ｐｙｔｈｏｎ   docs! C++"))
    assert context.authorized_at is None
    assert gate.classifier.calls == []
    authorized = await gate.service.authorize_online(
        context, "Ｐｙｔｈｏｎ   docs! C++", "search", budget="shared"
    )
    assert authorized.normalized_query == "Python docs! C++"
    assert authorized.purpose is SearchPurpose.TECHNICAL
    assert authorized.source_ids == frozenset({"python", "idna"})
    assert gate.classifier.calls == [("Python docs! C++", "shared")]
    assert await gate.service.classify_once(context, "Python docs! C++") is SearchPurpose.TECHNICAL
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(context, "Python docs! C++", "search")
    with pytest.raises(SearchPolicyDenied):
        await gate.service.classify_once(authorized, "Python asyncio")
    assert len(gate.classifier.calls) == 1


async def test_meme_uses_own_gate_and_image_sources_without_web_or_search(gate):
    gate.classifier.purpose = SearchPurpose.ORDINARY_MEME
    await gate.features.set_global(1, "web", False)
    await gate.features.set_global(1, "search", False)
    ctx = await gate.service.create_task_from_event(event(text="普通猫猫表情包"))
    authorized = await gate.service.authorize_online(ctx, "普通猫猫表情包", "online_meme")
    assert authorized.source_ids == frozenset({"meme"})
    result = await gate.service.register_result(
        authorized, "https://media.example.org/approved/cat.png", "meme"
    )
    assert (
        await gate.service.resolve_result(authorized, result, operation="image")
        == "https://media.example.org/approved/cat.png"
    )
    with pytest.raises(SearchPolicyDenied):
        await gate.service.revalidate(authorized, "read", source_id=result)


@pytest.mark.parametrize(
    "purpose",
    [
        SearchPurpose.GAMING,
        SearchPurpose.HOBBY,
        SearchPurpose.PRACTICAL,
        SearchPurpose.UNKNOWN,
        None,
        "technical",
        {"purpose": "technical"},
        TimeoutError(),
        RuntimeError(),
    ],
)
async def test_closed_uncertain_malformed_or_failed_classification_is_terminal(gate, purpose):
    gate.classifier.purpose = purpose
    ctx = await gate.service.create_task_from_event(event(text="资料查询"))
    for _ in range(2):
        with pytest.raises(SearchPolicyDenied):
            await gate.service.authorize_online(ctx, "资料查询", "search")
    assert len(gate.classifier.calls) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        "a" * 101,
        "Python\x00docs",
        "查找 QQ 123456789 的住址",
        "查找他人的手机号",
        "获取账号密码",
        "购买赃物",
        "诈骗教程",
        "成人图片",
        "血腥图片",
        "批量下载文档",
        "登录受限网站",
        "api_key=fictionalkey123",
        "Authorization: Bearer abcd",
        "QQ号123456",
        "https://example.org/login?token=secret",
        "password:secret",
        "user@example.org 联系方式",
        "site:example.org Python",
        "ＳＩＴＥ：example.org Python",
        "!google Python",
        "!!bing Python",
        "Python engine:google",
        "Python filetype:pdf",
        "Python pageno=2",
        "Python\n第二个查询",
        "Python; Java",
        "分十页搜索 Python",
        "技术资料和成人图片",
        "批量查询 Python",
    ],
)
async def test_unsafe_preflight_rejects_before_external_classifier(gate, text):
    ctx = await gate.service.create_task_from_event(event(text=text))
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, text, "search")
    assert gate.classifier.calls == []


async def test_normal_technical_symbols_and_single_line_normalization(gate):
    ctx = await gate.service.create_task_from_event(event(text="  C++  operator !  文档  "))
    await gate.service.preflight(ctx, "  C++  operator !  文档  ", "search")
    assert gate.classifier.calls == []


async def test_group_intersection_and_owner_narrowing_revoke_stale_context(gate):
    await gate.store.set_group_search_policy(
        34, GroupSearchPolicy(source_ids=frozenset({"python"}))
    )
    ctx = await gate.service.create_task_from_event(event())
    ctx = await gate.service.authorize_online(ctx, "Python docs", "search")
    assert ctx.source_ids == frozenset({"python"})
    policy = await gate.store.get_deployment_search_policy()
    await gate.store.set_deployment_search_policy(
        replace(
            policy,
            allowed_purposes=frozenset({"ordinary_meme"}),
            sources=tuple(s for s in policy.sources if s.purpose == "ordinary_meme"),
        )
    )
    with pytest.raises(SearchPolicyDenied):
        await gate.service.revalidate(ctx, "search")
    fresh = await gate.service.create_task_from_event(event())
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(fresh, "Python docs", "search")


@pytest.mark.parametrize(
    "change",
    ["group_policy", "deployment_policy", "consent", "group_gate", "global_gate", "ai_gate"],
)
async def test_recheck_revokes_after_policy_or_feature_change(gate, change):
    ctx = await gate.service.create_task_from_event(event())
    ctx = await gate.service.authorize_online(ctx, "Python docs", "search")
    if change == "group_policy":
        await gate.store.set_group_search_policy(34, GroupSearchPolicy())
    elif change == "deployment_policy":
        await gate.store.set_deployment_search_policy(
            await gate.store.get_deployment_search_policy()
        )
    elif change == "consent":
        await gate.store.set_search_consent(1, False)
    elif change == "global_gate":
        await gate.features.set_global(1, "search", False)
    else:
        await gate.store.set_feature_enabled(34, "ai" if change == "ai_gate" else "search", False)
    with pytest.raises(SearchPolicyDenied):
        await gate.service.revalidate(ctx, "search")


async def test_denied_gates_empty_sources_skip_classifier(gate):
    await gate.features.set_global(1, "search", False)
    ctx = await gate.service.create_task_from_event(event())
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, "Python docs", "search")
    assert gate.classifier.calls == []
    await gate.features.set_global(1, "search", True)
    await gate.store.set_group_search_policy(34, GroupSearchPolicy(source_ids=frozenset()))
    ctx = await gate.service.create_task_from_event(event())
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, "Python docs", "search")
    assert gate.classifier.calls == []


async def test_unissued_or_forged_handles_and_mutated_principal_are_denied(gate):
    principal = event()
    ctx = await gate.service.create_task_from_event(principal)
    for forged in (
        replace(ctx),
        replace(ctx, group_id=35),
        replace(ctx, user_id=1),
        ctx.task_id,
        SimpleNamespace(**ctx.__dict__),
    ):
        with pytest.raises(SearchPolicyDenied):
            await gate.service.preflight(forged, "Python docs", "search")
    principal.user_id = 1
    with pytest.raises(SearchPolicyDenied):
        await gate.service.preflight(ctx, "Python docs", "search")
    with pytest.raises(SearchPolicyDenied):
        await gate.service.create_task_from_event(
            SimpleNamespace(group_id=34, user_id=1, role="owner")
        )
    with pytest.raises(SearchPolicyDenied):
        await gate.service.create_task_from_event(event(99))


async def test_panel_session_authentication_recheck_and_forgery(gate):
    session = gate.auth.login(gate.settings.webui_admin_token.get_secret_value(), "test")
    ctx = await gate.service.create_task_from_panel(session, 34)
    ctx = await gate.service.authorize_online(ctx, "Python docs", "search")
    with pytest.raises(SearchPolicyDenied):
        await gate.service.create_task_from_panel(replace(session), 34)
    gate.auth.revoke(session.session_id)
    with pytest.raises(SearchPolicyDenied):
        await gate.service.revalidate(ctx, "search")


async def test_context_expires_and_explicit_revoke_rejects_handles(gate):
    ctx = await gate.service.create_task_from_event(event())
    gate.now[0] += 60
    with pytest.raises(SearchPolicyDenied):
        await gate.service.preflight(ctx, "Python docs", "search")
    ctx = await gate.service.create_task_from_event(event())
    gate.service.revoke_task(ctx.task_id)
    with pytest.raises(SearchPolicyDenied):
        await gate.service.preflight(ctx, "Python docs", "search")


async def test_parallel_classification_and_authorization_do_not_double_search(gate):
    ctx = await gate.service.create_task_from_event(event())
    results = await asyncio.gather(
        *(gate.service.authorize_online(ctx, "Python docs", "search") for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert len(gate.classifier.calls) == 1


async def test_classifier_missing_and_policy_change_during_classify_fail_closed(gate):
    missing = SearchPolicyService(gate.features)
    ctx = await missing.create_task_from_event(event())
    with pytest.raises(SearchPolicyDenied):
        await missing.authorize_online(ctx, "Python docs", "search")

    class RevokingClassifier:
        async def classify(self, text, *, budget):
            await gate.store.set_search_consent(1, False)
            return SearchPurpose.TECHNICAL

    gate.service.classifier = RevokingClassifier()
    ctx = await gate.service.create_task_from_event(event())
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, "Python docs", "search")


@pytest.mark.parametrize(
    "url,want,source",
    [
        (
            "https://DOCS.PYTHON.ORG./3/library/index.html",
            "https://docs.python.org/3/library/index.html",
            "python",
        ),
        ("https://docs.python.org:443/3", "https://docs.python.org/3", "python"),
        (
            "https://docs.python.org/3/library?id=42&lang=zh",
            "https://docs.python.org/3/library?id=42&lang=zh",
            "python",
        ),
        (
            "https://例子.中国/docs/公开",
            "https://xn--fsqu00a.xn--fiqs8s/docs/%E5%85%AC%E5%BC%80",
            "idna",
        ),
        (
            "https://docs.python.org/3/%E4%B8%AD%E6%96%87",
            "https://docs.python.org/3/%E4%B8%AD%E6%96%87",
            "python",
        ),
    ],
)
async def test_result_url_canonicalization_preserves_page_identity(gate, url, want, source):
    assert await gate.service.validate_result_url(url, SearchPurpose.TECHNICAL, source) == want


async def test_source_scheme_is_pinned_and_http_must_be_explicitly_configured(gate):
    with pytest.raises(SearchPolicyDenied):
        await gate.service.validate_result_url(
            "http://docs.python.org/3/", SearchPurpose.TECHNICAL, "python"
        )

    policy = await gate.store.get_deployment_search_policy()
    await gate.store.set_deployment_search_policy(
        replace(
            policy,
            sources=policy.sources
            + (SearchSourceRule("legacy-http", "technical", "legacy.example.org", scheme="http"),),
        )
    )
    assert (
        await gate.service.validate_result_url(
            "http://legacy.example.org/docs", SearchPurpose.TECHNICAL, "legacy-http"
        )
        == "http://legacy.example.org/docs"
    )
    with pytest.raises(SearchPolicyDenied):
        await gate.service.validate_result_url(
            "https://legacy.example.org/docs", SearchPurpose.TECHNICAL, "legacy-http"
        )


@pytest.mark.parametrize(
    "url",
    [
        "file:///3/docs",
        "https://user:pass@docs.python.org/3",
        "https://docs.python.org/3#fragment",
        "https://docs.python.org/3#",
        "https://docs.python.org:444/3",
        "https://sub.docs.python.org/3",
        "https://docs.python.org.evil.org/3",
        "https://docs.python.org/30",
        "https://docs.python.org/3/../private",
        "https://docs.python.org/3/%2e%2e/private",
        "https://docs.python.org/3/%252e%252e/private",
        "https://docs.python.org/3/a%2fb",
        "https://docs.python.org/3/a%5cb",
        "https://docs.python.org/3//x",
        "https://docs.python.org/3\\x",
        "https://docs.python.org/3/%00",
        "https://docs.python.org/3/%zz",
        "https://docs.python.org/3?token=secret",
        "https://docs.python.org/3?id=1&signature=abc",
        "https://docs.python.org/3?id=api_key%3Dsecret",
        "https://docs.python.org/3?unknown=1",
        "https://docs.python.org/3?%74oken=secret",
        "https://docs.python.org/3?id=https%3A%2F%2Fuser%3Apass%40example.org",
        "https://docs.python.org/3?id=one&id=two",
        "https://docs.python.org/3?id=abc%0Adef",
        "https://docs.python.org../3",
        "https://docs.python.org/3?lang=%252Fprivate",
    ],
)
async def test_url_boundary_and_sensitive_query_rejection(gate, url):
    with pytest.raises(SearchPolicyDenied):
        await gate.service.validate_result_url(url, SearchPurpose.TECHNICAL, "python")


async def test_private_literal_and_local_hosts_never_publish(gate):
    policy = await gate.store.get_deployment_search_policy()
    for host in ("127.0.0.1", "10.0.0.1", "localhost", "search", "foo.local"):
        try:
            await gate.store.set_deployment_search_policy(
                replace(policy, sources=(SearchSourceRule("private", "technical", host),))
            )
        except ValueError:
            # Structurally invalid local/literal hosts may be rejected by storage.
            continue
        with pytest.raises(SearchPolicyDenied):
            await gate.service.validate_result_url(
                f"http://{host}/", SearchPurpose.TECHNICAL, "private"
            )


async def test_result_ids_cannot_cross_tasks_groups_sources_or_be_guessed_urls(gate):
    ctx = await gate.service.create_task_from_event(event())
    ctx = await gate.service.authorize_online(ctx, "Python docs", "search")
    result = await gate.service.register_result(ctx, "https://docs.python.org/3?id=42", "python")
    assert await gate.service.resolve_result(ctx, result) == "https://docs.python.org/3?id=42"
    other = await gate.service.create_task_from_event(event(35))
    other = await gate.service.authorize_online(other, "Python docs", "search")
    for result_id in (result, "python", "https://docs.python.org/3", "guessed"):
        with pytest.raises(SearchPolicyDenied):
            await gate.service.resolve_result(other, result_id)
    with pytest.raises(SearchPolicyDenied):
        await gate.service.register_result(ctx, "https://media.example.org/approved/x.png", "meme")
    await gate.features.set_global(1, "web", False)
    with pytest.raises(SearchPolicyDenied):
        await gate.service.resolve_result(ctx, result)
    await gate.service.revalidate(ctx, "search")


@pytest.mark.parametrize(
    "raw,query,operation",
    [
        ("/search Python docs", "Python docs", "search"),
        ("/meme 普通猫猫表情包", "普通猫猫表情包", "online_meme"),
        ("/meme --mode online 普通猫猫表情包", "普通猫猫表情包", "online_meme"),
        ("/meme --mode auto 普通猫猫表情包", "普通猫猫表情包", "online_meme"),
    ],
)
async def test_trusted_command_query_extraction(gate, raw, query, operation):
    gate.classifier.purpose = (
        SearchPurpose.ORDINARY_MEME if operation == "online_meme" else SearchPurpose.TECHNICAL
    )
    ctx = await gate.service.create_task_from_event(event(text=raw))
    authorized = await gate.service.authorize_online(ctx, query, operation)
    assert authorized.normalized_query == query


@pytest.mark.parametrize(
    "raw,query",
    [
        ("Python docs", "Python security"),
        ("/search Python docs", "Python"),
        ("/search Python docs", "Python docs site:evil.org"),
        ("/other Python docs", "Python docs"),
    ],
)
async def test_query_cannot_be_rewritten_by_tool(gate, raw, query):
    ctx = await gate.service.create_task_from_event(event(text=raw))
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, query, "search")
    assert not gate.classifier.calls


@pytest.mark.parametrize(
    "url",
    [
        "\nhttps://docs.python.org/3",
        "https://docs.python.org/3\t",
        "https://docs.python.org/3\n",
        "https://docs.python.org/3/a%2520b",
        "https://docs.python.org/3/a%25b",
        "https://docs.python.org/3/%7f",
        "https://docs.python.org/3?id=%FF",
        "https://docs.python.org/3?id=%zz",
        "https://docs.python.org/3?id=abc%250Adef",
    ],
)
async def test_parser_controls_and_nested_encoding_do_not_change_url_identity(gate, url):
    with pytest.raises(SearchPolicyDenied):
        await gate.service.validate_result_url(url, SearchPurpose.TECHNICAL, "python")


async def test_read_requires_opaque_result_not_configured_source_or_missing_id(gate):
    ctx = await gate.service.create_task_from_event(event())
    ctx = await gate.service.authorize_online(ctx, "Python docs", "search")
    for source_id in (None, "python"):
        with pytest.raises(SearchPolicyDenied):
            await gate.service.revalidate(ctx, "read", source_id)
    result = await gate.service.register_result(ctx, "https://docs.python.org/3", "python")
    await gate.service.revalidate(ctx, "read", result)


@pytest.mark.parametrize("operation", ["read", "image"])
async def test_read_or_image_cannot_authorize_initial_search(gate, operation):
    ctx = await gate.service.create_task_from_event(event())
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, "Python docs", operation)
    assert not gate.classifier.calls


async def test_group_limit_shortens_registry_lifetime(gate):
    from aling.core.storage import SearchLimits

    await gate.store.set_group_search_policy(
        34, GroupSearchPolicy(limits=SearchLimits(task_seconds=30, work_seconds=27))
    )
    ctx = await gate.service.create_task_from_event(event())
    gate.now[0] += 30
    with pytest.raises(SearchPolicyDenied):
        await gate.service.preflight(ctx, "Python docs", "search")


async def test_parallel_classify_once_uses_one_attempt_and_meme_operation(gate):
    gate.classifier.purpose = SearchPurpose.ORDINARY_MEME
    ctx = await gate.service.create_task_from_event(event(text="普通猫猫表情包"))
    ctx = await gate.service.authorize_online(ctx, "普通猫猫表情包", "online_meme")
    await gate.features.set_global(1, "search", False)
    assert await gate.service.classify_once(ctx, "普通猫猫表情包") is SearchPurpose.ORDINARY_MEME
    assert len(gate.classifier.calls) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Python (site:example.org)",
        "Python engines=google",
        "Python categories=images",
        "Python ! google",
        "Python token=fictionalsecret",
        "Python cookie=session123",
        "steal credentials",
        "dox someone's private address",
    ],
)
async def test_obvious_operator_and_credential_forms_never_reach_classifier(gate, text):
    ctx = await gate.service.create_task_from_event(event(text=text))
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, text, "search")
    assert not gate.classifier.calls


async def test_pending_parallel_classification_reuses_one_verdict(gate):
    started = asyncio.Event()

    class DelayedClassifier(Classifier):
        async def classify(self, text, *, budget):
            started.set()
            await asyncio.sleep(0.2)
            return await super().classify(text, budget=budget)

    gate.service.classifier = classifier = DelayedClassifier()
    ctx = await gate.service.create_task_from_event(event())
    first = asyncio.create_task(gate.service.classify_once(ctx, "Python docs"))
    await started.wait()
    verdicts = await asyncio.gather(first, gate.service.classify_once(ctx, "Python docs"))
    assert verdicts == [SearchPurpose.TECHNICAL, SearchPurpose.TECHNICAL]
    assert len(classifier.calls) == 1


async def test_mixed_uncertain_request_is_denied_and_never_reclassified(gate):
    gate.classifier.purpose = SearchPurpose.UNKNOWN
    ctx = await gate.service.create_task_from_event(event(text="Python 文档和游戏攻略"))
    for _ in range(2):
        with pytest.raises(SearchPolicyDenied):
            await gate.service.authorize_online(ctx, "Python 文档和游戏攻略", "search")
    assert len(gate.classifier.calls) == 1


async def test_meme_attribution_pages_and_image_sources_have_distinct_result_kinds(gate):
    deployment = await gate.store.get_deployment_search_policy()
    await gate.store.set_deployment_search_policy(
        replace(
            deployment,
            sources=deployment.sources
            + (SearchSourceRule("meme_page", "ordinary_meme", "meme.example.org", "/approved"),),
        )
    )
    gate.classifier.purpose = SearchPurpose.ORDINARY_MEME
    ctx = await gate.service.create_task_from_event(event(text="普通猫猫表情包"))
    ctx = await gate.service.authorize_online(ctx, "普通猫猫表情包", "online_meme")
    assert ctx.source_ids == frozenset({"meme", "meme_page"})
    page = await gate.service.register_result(
        ctx, "https://meme.example.org/approved/cat", "meme_page"
    )
    image = await gate.service.register_result(
        ctx, "https://media.example.org/approved/cat.png", "meme"
    )
    assert (
        await gate.service.resolve_result(ctx, page, operation="online_meme")
        == "https://meme.example.org/approved/cat"
    )
    assert (
        await gate.service.resolve_result(ctx, image, operation="image")
        == "https://media.example.org/approved/cat.png"
    )
    for operation in ("read", "image"):
        with pytest.raises(SearchPolicyDenied):
            await gate.service.resolve_result(ctx, page, operation=operation)


async def test_meme_page_only_configuration_cannot_classify_or_search_images(gate):
    deployment = await gate.store.get_deployment_search_policy()
    await gate.store.set_deployment_search_policy(
        replace(
            deployment,
            sources=(SearchSourceRule("meme_page", "ordinary_meme", "meme.example.org"),),
        )
    )
    gate.classifier.purpose = SearchPurpose.ORDINARY_MEME
    ctx = await gate.service.create_task_from_event(event(text="普通猫猫表情包"))
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, "普通猫猫表情包", "online_meme")
    assert not gate.classifier.calls


@pytest.mark.parametrize(
    "url",
    [
        "https://docs.python.org/3/%EF%BB%BFhidden",
        "https://docs.python.org/3?id=abc%7Fdef",
    ],
)
async def test_url_invisible_controls_never_publish(gate, url):
    with pytest.raises(SearchPolicyDenied):
        await gate.service.validate_result_url(url, SearchPurpose.TECHNICAL, "python")


async def test_event_user_group_mutation_during_classification_cannot_authorize(gate):
    principal = event()

    class MutatingClassifier:
        async def classify(self, text, *, budget):
            principal.group_id = 35
            return SearchPurpose.TECHNICAL

    gate.service.classifier = MutatingClassifier()
    ctx = await gate.service.create_task_from_event(principal)
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, "Python docs", "search")


async def test_classifier_timeout_uses_group_narrowed_policy(gate):
    from aling.core.storage import SearchLimits

    await gate.store.set_group_search_policy(
        34, GroupSearchPolicy(limits=SearchLimits(request_timeout_seconds=1))
    )

    class SlowClassifier:
        async def classify(self, text, *, budget):
            await asyncio.sleep(1.1)
            return SearchPurpose.TECHNICAL

    gate.service.classifier = SlowClassifier()
    ctx = await gate.service.create_task_from_event(event())
    with pytest.raises(SearchPolicyDenied):
        await gate.service.authorize_online(ctx, "Python docs", "search")
