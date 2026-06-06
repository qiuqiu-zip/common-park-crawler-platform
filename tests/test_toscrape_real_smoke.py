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
from crawler_platform.html_query import HTMLDocument
from crawler_platform.http_client import FakeFetcher
from crawler_platform.storage import FileStore


BOOKS_EXAMPLE = "examples/real_books_toscrape_small.json"
BOOKS_PAGE = Path("examples/fixtures/books_toscrape_page_1.html")
BOOKS_DETAIL = Path("examples/fixtures/books_toscrape_detail_1.html")
START_URL = "https://books.toscrape.com/"


def test_books_toscrape_example_debug_extracts_list_and_detail_fields():
    spider = load_spider_config(BOOKS_EXAMPLE)

    preview = debug_extract(spider, input_file=BOOKS_PAGE, single_url=START_URL, sample_size=2)
    first = preview["sample_records"][0]

    assert preview["scope_count"] == 6
    assert first["title"] == "A Light in the Attic"
    assert first["rating"] == "Three"
    assert first["availability"] == "In stock"
    assert first["detail_url"] == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    assert all(item["status"] == "ok" for item in preview["field_quality"])

    detail_record = extract_fields(
        HTMLDocument(BOOKS_DETAIL.read_text(encoding="utf-8")).root,
        spider.detail.fields,
        context={"source_url": first["detail_url"]},
    )
    assert detail_record == {
        "title": "A Light in the Attic",
        "price": "GBP51.77",
        "availability": "In stock",
        "rating": "Three",
        "product_description": "Poems and drawings with sharp little jokes and gentle surprises.",
        "upc": "a897fe39b1053632",
        "category": "Poetry",
    }


def test_books_toscrape_plan_includes_detail_request_estimate():
    spider = load_spider_config(BOOKS_EXAMPLE)

    plan = build_crawl_plan(spider)

    assert plan["safe_to_run"] is True
    assert plan["estimated_max_requests"] == 7
    assert plan["crawl_policy"]["allowed_domains"] == ["books.toscrape.com"]
    assert plan["robots_checks"][0]["status"] == "unavailable"
    assert "robots unavailable for books.toscrape.com" in plan["warnings"]


def test_books_toscrape_small_run_limits_detail_fetches_before_export(workspace_tmp_path):
    spider = load_spider_config(BOOKS_EXAMPLE)
    detail_html = BOOKS_DETAIL.read_text(encoding="utf-8")
    fetcher = FakeFetcher(
        {
            START_URL: BOOKS_PAGE.read_text(encoding="utf-8"),
            "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html": detail_html,
            "https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html": detail_html.replace("A Light in the Attic", "Tipping the Velvet").replace("a897fe39b1053632", "90fa61229261140a"),
            "https://books.toscrape.com/catalogue/soumission_998/index.html": detail_html.replace("A Light in the Attic", "Soumission").replace("a897fe39b1053632", "6957f44c3847a760"),
            "https://books.toscrape.com/catalogue/sharp-objects_997/index.html": detail_html.replace("A Light in the Attic", "Sharp Objects").replace("a897fe39b1053632", "e00eb4fd7b871a48"),
            "https://books.toscrape.com/catalogue/sapiens_996/index.html": detail_html.replace("A Light in the Attic", "Sapiens").replace("a897fe39b1053632", "4165285e1663650f"),
            "https://books.toscrape.com/catalogue/page-2.html": "<html><body>should not fetch page two</body></html>",
        }
    )
    store = FileStore(workspace_tmp_path)

    task = CrawlerEngine(store=store, fetcher=fetcher, sleep=lambda _: None).run(spider, task_id="books-local-run")
    records = store.read_records("books-local-run")

    assert task.saved_records == spider.pagination.max_records == 5
    assert task.total_requests == 6
    assert [request.url for request in fetcher.requests] == [
        START_URL,
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        "https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html",
        "https://books.toscrape.com/catalogue/soumission_998/index.html",
        "https://books.toscrape.com/catalogue/sharp-objects_997/index.html",
        "https://books.toscrape.com/catalogue/sapiens_996/index.html",
    ]
    assert records[0]["category"] == "Poetry"
    assert records[0]["detail_url"].startswith("https://books.toscrape.com/catalogue/")

    manifest = ExportService(store).export_task("books-local-run", fmt="csv")
    csv_path = Path(manifest["path"])
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        exported_rows = list(csv.DictReader(handle))
    assert manifest["rows_count"] == task.saved_records
    assert len(exported_rows) == task.saved_records

    report = store.get_run_report("task", "books-local-run")
    assert report["crawl_policy"]["policy_checked_urls"] == 6
    assert report["record_quality_status"] == "ok"
    assert report["saved_records"] == task.saved_records


def test_toscrape_trial_keeps_database_dependency_boundary():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    text += "\n".join(path.read_text(encoding="utf-8") for path in Path("src").rglob("*.py"))
    assert not re.search(
        r"\b(sqlite|sqlalchemy|mysql|postgres|psycopg)\b|\b(from|import)\b.*\borm\b|\borm\b.*\b(from|import)\b",
        text,
        re.IGNORECASE,
    )
