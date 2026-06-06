import csv
import json
import re
from pathlib import Path

from crawler_platform.config_loader import load_spider_config
from crawler_platform.crawl_policy import build_crawl_plan
from crawler_platform.debugging import debug_extract
from crawler_platform.engine import CrawlerEngine
from crawler_platform.exporter import ExportService
from crawler_platform.extractor import extract_fields
from crawler_platform.http_client import FakeFetcher
from crawler_platform.storage import FileStore


PEERTUBE_EXAMPLE = "examples/real_peertube_public_small.json"
PEERTUBE_LIST = Path("examples/fixtures/peertube_public_videos.json")
PEERTUBE_DETAIL_1 = Path("examples/fixtures/peertube_public_detail_1.json")
PEERTUBE_DETAIL_2 = Path("examples/fixtures/peertube_public_detail_2.json")
START_URL = "https://video.blender.org/api/v1/videos?start=0&count=10&sort=-publishedAt"
DETAIL_1_URL = "https://video.blender.org/api/v1/videos/bd0084a5-1d26-4816-ab5e-1bad9e2fb990"
DETAIL_2_URL = "https://video.blender.org/api/v1/videos/11111111-2222-4333-8444-555555555555"


def test_peertube_example_debug_extracts_public_video_fields():
    spider = load_spider_config(PEERTUBE_EXAMPLE)

    preview = debug_extract(spider, input_file=PEERTUBE_LIST, single_url=START_URL, sample_size=2)
    first = preview["sample_records"][0]

    assert preview["scope_count"] == 2
    assert first["title"] == "WING IT! - Blender Open Movie"
    assert first["channel"] == "Blender Open Movies"
    assert first["privacy"] == "Public"
    assert first["watch_url"] == "/videos/watch/bd0084a5-1d26-4816-ab5e-1bad9e2fb990"
    assert first["thumbnail_url"] == "/lazy-static/thumbnails/wing-it.jpg"
    assert first["detail_api_url"] == DETAIL_1_URL
    assert all(item["status"] == "ok" for item in preview["field_quality"])

    detail_record = extract_fields(
        json.loads(PEERTUBE_DETAIL_1.read_text(encoding="utf-8")),
        spider.detail.fields,
        context={"source_url": first["detail_api_url"]},
    )
    assert detail_record == {
        "full_description": "A public Blender open movie published on a PeerTube instance. This fixture keeps only video metadata needed for smoke testing.",
        "tags": "OpenMovie,b3d,blender3d",
        "likes": 12,
        "downloads": 3,
        "comments": 0,
        "license": "Attribution",
        "category": "Films",
        "language": "English",
    }


def test_peertube_plan_uses_small_real_external_boundaries():
    spider = load_spider_config(PEERTUBE_EXAMPLE)

    plan = build_crawl_plan(spider)

    assert plan["safe_to_run"] is True
    assert plan["estimated_max_requests"] == 11
    assert plan["crawl_policy"]["allowed_domains"] == ["video.blender.org"]
    assert plan["crawl_policy"]["allow_cross_domain"] is False
    assert spider.crawl_policy.max_requests == 20
    assert spider.rate_limit.requests_per_second == 0.5
    assert spider.concurrency.max_concurrent_requests == 1
    assert plan["robots_checks"][0]["status"] == "unavailable"
    assert "robots unavailable for video.blender.org" in plan["warnings"]


def test_peertube_small_run_limits_detail_fetches_and_exports(workspace_tmp_path):
    spider = load_spider_config(PEERTUBE_EXAMPLE)
    fetcher = FakeFetcher(
        {
            START_URL: PEERTUBE_LIST.read_text(encoding="utf-8"),
            DETAIL_1_URL: PEERTUBE_DETAIL_1.read_text(encoding="utf-8"),
            DETAIL_2_URL: PEERTUBE_DETAIL_2.read_text(encoding="utf-8"),
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher, sleep=lambda _: None).run(spider, task_id="peertube-local-run")
    records = store.read_records("peertube-local-run")

    assert task.saved_records == 2
    assert task.total_requests == 3
    assert [request.url for request in fetcher.requests] == [START_URL, DETAIL_1_URL, DETAIL_2_URL]
    assert records[0]["tags"] == "OpenMovie,b3d,blender3d"
    assert records[0]["license"] == "Attribution"
    assert records[1]["category"] == "Art"
    assert all(record["watch_url"].startswith("/videos/watch/") for record in records)

    manifest = ExportService(store).export_task("peertube-local-run", fmt="csv")
    csv_path = Path(manifest["path"])
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        exported_rows = list(csv.DictReader(handle))
    assert manifest["rows_count"] == task.saved_records
    assert len(exported_rows) == task.saved_records

    report = store.get_run_report("task", "peertube-local-run")
    assert report["crawl_policy"]["policy_checked_urls"] == 3
    assert report["record_quality_status"] == "ok"
    assert report["saved_records"] == task.saved_records


def test_peertube_trial_keeps_database_dependency_boundary():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    text += "\n".join(path.read_text(encoding="utf-8") for path in Path("src").rglob("*.py"))
    assert not re.search(
        r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b",
        text,
        re.IGNORECASE,
    )
