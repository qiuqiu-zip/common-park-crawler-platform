import json
from pathlib import Path

from crawler_platform.cli import main
from crawler_platform.config_loader import load_spider_config
from crawler_platform.debugging import run_dry_run
from crawler_platform.extractor import extract_fields
from crawler_platform.html_query import HTMLDocument
from crawler_platform.http_client import FakeFetcher, HttpResponse, HttpStatusError
from crawler_platform.models import FieldRule, RequestOptions, SpiderConfig
from crawler_platform.storage import FileStore


def test_dry_run_outputs_preview_diagnostics_and_avoids_formal_state(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = load_spider_config("examples/debug_dry_run.json")

    report = run_dry_run(spider, store, save_report=True)

    assert report.item_count == 2
    assert report.sample_records[0]["title"] == "Clean Crawler Design"
    assert report.field_diagnostics[0].field == "title"
    assert report.field_diagnostics[0].match_count == 2
    assert report.report_path
    assert Path(report.report_path).exists()
    for formal_dir in (store.spiders_dir, store.tasks_dir, store.results_dir, store.hashes_dir, store.watermarks_dir, store.checkpoints_dir):
        assert not any(path.is_file() for path in formal_dir.rglob("*"))


def test_dry_run_reports_required_missing_and_field_quality(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    spider = load_spider_config("examples/debug_quality_report.json")

    report = run_dry_run(spider, store)

    title_diag = next(item for item in report.field_diagnostics if item.field == "title")
    assert title_diag.match_count == 2
    assert title_diag.missing_count == 1
    assert "required field title missing" in (title_diag.warning or "")
    title_quality = next(item for item in report.field_quality if item.field == "title")
    assert title_quality.total_records == 3
    assert title_quality.non_empty_count == 2
    assert title_quality.status == "warning"


def test_cleaning_and_url_transforms_use_source_url_context():
    html = """
    <article>
      <a class="detail" href="items/1?utm=debug">Detail</a>
      <span class="price">USD 1,299.50</span>
      <span class="views">1.2万</span>
      <div class="body"><p>HTML <strong>text</strong></p></div>
      <span class="sku">SKU: ABC-123</span>
    </article>
    """
    rules = [
        FieldRule(name="detail_url", type="attr", selector=".detail", attribute="href", transforms=["urljoin", "strip_query"]),
        FieldRule(name="price", type="css", selector=".price", transforms=["currency_parse"]),
        FieldRule(name="views", type="css", selector=".views", transforms=["number_parse"]),
        FieldRule(name="body", type="css", selector=".body", transforms=["html_to_text"]),
        FieldRule(name="sku", type="css", selector=".sku", transforms=[{"type": "regex_extract", "pattern": r"SKU:\s*([A-Z0-9-]+)", "group": 1}]),
        FieldRule(name="fallback", type="css", selector=".missing", transforms=[{"type": "default_if_empty", "value": "n/a"}]),
    ]

    record = extract_fields(HTMLDocument(html).root, rules, context={"source_url": "https://example.test/catalog/index.html"})

    assert record["detail_url"] == "https://example.test/catalog/items/1"
    assert record["price"] == 1299.5
    assert record["views"] == 12000
    assert record["body"] == "HTML text"
    assert record["sku"] == "ABC-123"
    assert record["fallback"] == "n/a"


def test_failed_fetch_saves_redacted_debug_artifact(workspace_tmp_path):
    store = FileStore(workspace_tmp_path)
    response = HttpResponse(
        url="https://example.test/private",
        final_url="https://example.test/private",
        status_code=500,
        body="<html>failed</html>",
        headers={"Authorization": "Bearer raw-token", "Set-Cookie": "sid=raw-cookie"},
    )
    spider = SpiderConfig(
        id="failed-debug",
        name="failed-debug",
        start_urls=["https://example.test/private"],
        request=RequestOptions(response_type="html", headers={"Authorization": "Bearer raw-token"}, cookies={"sid": "raw-cookie"}),
        fields=[FieldRule(name="title", type="css", selector="h1")],
    )
    fetcher = FakeFetcher({"https://example.test/private": HttpStatusError(response)})

    report = run_dry_run(spider, store, fetcher=fetcher)

    assert report.errors
    assert report.artifact_path
    metadata = json.loads((Path(report.artifact_path) / "metadata.json").read_text(encoding="utf-8"))
    text = json.dumps(metadata, ensure_ascii=False)
    assert "***REDACTED***" in text
    assert "raw-token" not in text
    assert "raw-cookie" not in text
    assert any(Path(item["failed_page_path"]).exists() for item in metadata["artifacts"])


def test_dry_run_cli_and_debug_alias_emit_json(workspace_tmp_path, capsys):
    assert main(["dry-run", "examples/debug_dry_run.json", "--data-dir", str(workspace_tmp_path / "cli"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["item_count"] == 2
    assert payload["field_diagnostics"][0]["field"] == "title"

    assert main(["debug", "dry-run", "examples/debug_urljoin.json", "--data-dir", str(workspace_tmp_path / "debug-cli"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sample_records"][0]["detail_url"].endswith("clean-crawler.html")
