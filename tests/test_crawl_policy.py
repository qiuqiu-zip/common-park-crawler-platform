import json

from crawler_platform.cli import main
from crawler_platform.crawl_policy import CrawlPolicyEngine, build_crawl_plan, normalize_url
from crawler_platform.engine import CrawlerEngine
from crawler_platform.http_client import FakeFetcher
from crawler_platform.models import CrawlPolicyConfig, DetailOptions, FieldRule, PaginationOptions, RequestOptions, RobotsPolicyConfig, SpiderConfig, TaskStatus
from crawler_platform.storage import FileStore
from crawler_platform.validation import ensure_valid_spider_config, validate_spider_config


def _spider(**overrides):
    spider = SpiderConfig(
        id="policy-demo",
        name="Policy Demo",
        type="http",
        start_urls=["https://example.test/list?utm_source=ad&category=books#top"],
        item_selector="article.item",
        request=RequestOptions(response_type="html"),
        fields=[
            FieldRule(name="title", type="css", selector=".title", required=True),
            FieldRule(name="detail_path", type="attr", selector=".title", attribute="href"),
        ],
    )
    for key, value in overrides.items():
        setattr(spider, key, value)
    return spider


def _policy(**overrides):
    policy = CrawlPolicyConfig(enabled=True)
    for key, value in overrides.items():
        setattr(policy, key, value)
    return policy


def test_old_configs_validate_without_enabling_policy():
    spider = ensure_valid_spider_config(
        {
            "id": "old",
            "name": "Old",
            "version": "1.0",
            "type": "api",
            "start_urls": ["https://example.test/api"],
            "items_json_path": "items",
            "fields": [{"name": "id", "type": "json_path", "json_path": "id"}],
        }
    )

    assert spider.crawl_policy.enabled is False


def test_invalid_robots_mode_is_rejected():
    config = _spider().to_dict()
    config["crawl_policy"] = {"robots": {"mode": "audit"}}

    result = validate_spider_config(config)

    assert not result.valid
    assert any(issue.path == "crawl_policy.robots.mode" for issue in result.issues)


def test_normalize_strips_fragment_and_tracking_but_keeps_business_params():
    policy = _policy()

    normalized = normalize_url("https://EXAMPLE.test/list?utm_source=ad&category=books&page=2&fbclid=x#top", policy)

    assert normalized == "https://example.test/list?category=books&page=2"


def test_scope_denied_cross_domain_include_and_exclude_rules():
    spider = _spider()
    spider.crawl_policy = _policy(
        allowed_domains=["example.test"],
        denied_domains=["bad.example.test"],
        include_url_patterns=[r"/list|/detail"],
        exclude_url_patterns=[r"/blocked"],
    )
    engine = CrawlPolicyEngine(spider)

    assert engine.check_url("https://example.test/detail/1", kind="detail", depth=1).allowed
    assert engine.check_url("https://other.test/detail/1", kind="detail", depth=1).blocked
    assert engine.check_url("https://bad.example.test/detail/1", kind="detail", depth=1).blocked
    assert engine.check_url("https://example.test/blocked", kind="detail", depth=1).blocked
    assert engine.summary()["policy_blocked_urls"] == 3


def test_allow_cross_domain_still_honors_denied_domains():
    spider = _spider()
    spider.crawl_policy = _policy(allow_cross_domain=True, denied_domains=["bad.example.test"])
    engine = CrawlPolicyEngine(spider)

    assert engine.check_url("https://other.test/detail/1", kind="detail", depth=1).allowed
    assert engine.check_url("https://bad.example.test/detail/1", kind="detail", depth=1).blocked


def test_robots_warn_block_and_unavailable_policy():
    warn_spider = _spider()
    warn_spider.start_urls = ["https://crawler.local/list"]
    warn_spider.crawl_policy = _policy(robots=RobotsPolicyConfig(mode="warn", rules={"crawler.local": "User-agent: *\nDisallow: /private\n"}))
    warn = CrawlPolicyEngine(warn_spider).check_url("https://crawler.local/private", kind="detail", depth=1)

    block_spider = _spider()
    block_spider.start_urls = ["https://crawler.local/list"]
    block_spider.crawl_policy = _policy(robots=RobotsPolicyConfig(mode="block", rules={"crawler.local": "User-agent: *\nDisallow: /private\n"}))
    blocked = CrawlPolicyEngine(block_spider).check_url("https://crawler.local/private", kind="detail", depth=1)

    unavailable_spider = _spider()
    unavailable_spider.start_urls = ["https://crawler.local/list"]
    unavailable_spider.crawl_policy = _policy(robots=RobotsPolicyConfig(unavailable_policy="warn"))
    unavailable = CrawlPolicyEngine(unavailable_spider).check_url("https://crawler.local/list", kind="start_url", depth=0)

    assert warn.allowed and warn.warnings
    assert blocked.blocked and blocked.violations
    assert unavailable.allowed and unavailable.warnings


def test_engine_applies_policy_to_pagination_and_skips_blocked_url(workspace_tmp_path):
    spider = _spider()
    spider.start_urls = ["https://example.test/list"]
    spider.pagination = PaginationOptions(type="next_button", next_selector="a.next", next_attribute="href", max_pages=2)
    spider.crawl_policy = _policy(exclude_url_patterns=[r"/blocked"])
    fetcher = FakeFetcher(
        {
            "https://example.test/list": """
            <article class="item"><a class="title">A</a></article>
            <a class="next" href="https://example.test/blocked">Next</a>
            """,
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="policy-pagination")

    assert task.status == TaskStatus.SUCCESS
    assert task.total_requests == 1
    assert [request.url for request in fetcher.requests] == ["https://example.test/list"]
    assert task.request_governance["crawl_policy"]["policy_blocked_urls"] == 1


def test_engine_applies_policy_to_detail_and_keeps_parent_record(workspace_tmp_path):
    spider = _spider()
    spider.start_urls = ["https://example.test/list"]
    spider.detail = DetailOptions(enabled=True, url_field="detail_path", fields=[FieldRule(name="body", type="css", selector=".body")])
    spider.crawl_policy = _policy(exclude_url_patterns=[r"/blocked"])
    fetcher = FakeFetcher({"https://example.test/list": '<article class="item"><a class="title" href="/blocked">A</a></article>'})
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="policy-detail")

    assert task.status == TaskStatus.SUCCESS
    assert task.total_requests == 1
    assert store.read_records("policy-detail")[0]["title"] == "A"
    assert "body" not in store.read_records("policy-detail")[0]


def test_plan_output_and_cli_json(workspace_tmp_path, capsys):
    spider = _spider()
    spider.crawl_policy = _policy(exclude_url_patterns=[r"/blocked"])
    spider.pagination = PaginationOptions(type="url_list", urls=["https://example.test/blocked"], max_pages=2)

    plan = build_crawl_plan(spider)

    assert plan["spider_id"] == "policy-demo"
    assert plan["normalized_start_urls"][0] == "https://example.test/list?category=books"
    assert plan["blocked_urls"]
    assert plan["safe_to_run"] is False

    assert main(["--data-dir", str(workspace_tmp_path / "plan"), "plan", "examples/crawl_policy_local.json", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["crawl_policy"]["enabled"] is True


def test_debug_selector_and_extract_cli_emit_quality_json(workspace_tmp_path, capsys):
    assert main(["--data-dir", str(workspace_tmp_path / "selector"), "debug", "selector", "examples/fixtures/crawl_policy_page.html", "--selector", ".title", "--selector-type", "css", "--json"]) == 0
    selector_payload = json.loads(capsys.readouterr().out)
    assert selector_payload["matched_count"] == 1
    assert selector_payload["sample_values"][0] == "Safe Local Item"

    assert main(["--data-dir", str(workspace_tmp_path / "extract"), "debug", "extract", "examples/debug_extract_local.json", "--input-file", "examples/fixtures/debug_extract_items.json", "--json"]) == 0
    extract_payload = json.loads(capsys.readouterr().out)
    assert extract_payload["scope_count"] == 2
    assert extract_payload["field_quality"][0]["status"] == "ok"
    assert extract_payload["sample_records"][0]["title"] == "Policy Primer"


def test_task_report_includes_policy_and_record_quality(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = _spider()
    spider.start_urls = ["https://example.test/list"]
    spider.fields = [FieldRule(name="title", type="css", selector=".title", required=True)]
    spider.crawl_policy = _policy()
    fetcher = FakeFetcher({"https://example.test/list": '<article class="item"><a class="title">A</a></article>'})

    task = CrawlerEngine(store=store, fetcher=fetcher).run(spider, task_id="policy-report")
    report = store.get_run_report("task", "policy-report")

    assert task.status == TaskStatus.SUCCESS
    assert report["record_quality_status"] == "ok"
    assert report["field_quality"][0]["field"] == "title"
    assert report["duplicate_rate"] == 0.0
    assert report["crawl_policy"]["policy_checked_urls"] == 1
