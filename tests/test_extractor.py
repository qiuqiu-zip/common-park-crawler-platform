import pytest

from crawler_platform.config_loader import load_spider_config
from crawler_platform.extractor import (
    RequiredFieldMissingError,
    extract_fields,
    extract_records,
    extract_value,
    iter_records,
    json_path,
    merge_records,
)
from crawler_platform.html_query import HTMLDocument
from crawler_platform.models import FieldRule, SpiderConfig


def test_extract_html_fields_with_css_xpath_regex_and_attribute():
    html = """
    <section>
      <article class="item" data-id="42">
        <a class="title" href="/detail/42">Example title</a>
        <span class="price">$19.50</span>
      </article>
    </section>
    """
    spider = SpiderConfig(
        id="html",
        name="html",
        start_urls=["https://example.test"],
        item_selector="article.item",
        fields=[
            FieldRule(name="title", type="css", selector="a.title"),
            FieldRule(name="detail_url", type="attribute", selector="a.title", attribute="href"),
            FieldRule(name="item_id", type="xpath", selector='//article[@id="missing"]/@data-id', default="none"),
            FieldRule(name="price", type="regex", pattern=r"\$(\d+\.\d+)"),
        ],
    )

    records = extract_records(html, spider)

    assert records == [
        {"title": "Example title", "detail_url": "/detail/42", "item_id": "none", "price": "19.50"}
    ]


def test_json_path_extraction_for_api_items():
    spider = SpiderConfig(
        id="api",
        name="api",
        start_urls=["https://example.test/api"],
        items_json_path="data.items",
        fields=[
            FieldRule(name="title", type="json_path", json_path="title"),
            FieldRule(name="score", type="json_path", json_path="metrics.score"),
        ],
    )

    records = extract_records('{"data":{"items":[{"title":"A","metrics":{"score":9}}]}}', spider)

    assert records == [{"title": "A", "score": 9}]


def test_xpath_string_expression():
    document = HTMLDocument("<main><h1>Hello</h1></main>")

    value = extract_value(document.root, FieldRule(name="heading", type="xpath", selector="string(//h1)"))

    assert value == "Hello"


def test_extract_records_from_five_example_configs():
    cases = [
        (
            "examples/html_list.json",
            """
            <article class="product">
              <a class="title" href="/p/1">Widget</a>
              <span class="price">$12.50</span>
            </article>
            """,
            {"title": "Widget", "detail_path": "/p/1", "price": "12.50"},
        ),
        (
            "examples/api_json.json",
            '{"data":{"items":[{"id":"p1","title":"Widget","links":{"detail":"/p/1"}}]}}',
            {"id": "p1", "title": "Widget", "detail_path": "/p/1"},
        ),
        (
            "examples/detail_follow.json",
            '<article class="summary"><a class="title" href="/n/1">News</a></article>',
            {"title": "News", "detail_path": "/n/1"},
        ),
        (
            "examples/dedup.json",
            '{"data":{"rows":[{"sku":"S-1","name":"Widget"}]}}',
            {"sku": "S-1", "name": "Widget"},
        ),
        (
            "examples/extractor_showcase.json",
            """
            <article class="product">
              <a class="title" href="/show/1">  excellent widget  </a>
              <span class="price">$19.50</span>
              <span class="tag">Featured</span><span class="tag">Sale</span>
              <div class="seller"><span class="name"> Acme </span><span>rating: 4.5</span></div>
            </article>
            """,
            {
                "title": "Excellent Widget",
                "detail_url": "/show/1",
                "price": 19.5,
                "tags": "featured,sale",
                "seller": {"name": "Acme", "rating": 4.5},
            },
        ),
    ]

    for config_path, content, expected in cases:
        spider = load_spider_config(config_path)
        assert extract_records(content, spider)[0] == expected


def test_json_path_collects_values_from_arrays():
    payload = {"items": [{"title": "A"}, {"title": "B"}]}

    assert json_path(payload, "items[].title") == ["A", "B"]


def test_transforms_join_with_and_attr_alias():
    html = '<article><a href="/x"> Mixed CASE </a><span class="tag">One</span><span class="tag">Two</span></article>'
    document = HTMLDocument(html)
    rules = [
        FieldRule(name="url", type="attr", selector="a", attribute="href"),
        FieldRule(name="title", type="css", selector="a", transforms=["strip", "lower"]),
        FieldRule(name="tags", type="css", selector="span.tag", many=True, transforms=["upper"], join_with="|"),
    ]

    assert extract_fields(document.root, rules) == {"url": "/x", "title": "mixed case", "tags": "ONE|TWO"}


def test_css_attribute_selectors_support_common_operators():
    html = """
    <section>
      <a class="video primary" href="/video/BV123" data-kind="video-card" lang="zh-CN">Video</a>
      <a class="video secondary" href="/audio/AV456" data-kind="audio-card" lang="en">Audio</a>
    </section>
    """
    document = HTMLDocument(html)

    assert [node.text for node in document.select('a[href*="/video/"]')] == ["Video"]
    assert [node.text for node in document.select('a[href^="/video/"]')] == ["Video"]
    assert [node.text for node in document.select('a[href$="BV123"]')] == ["Video"]
    assert [node.text for node in document.select('a[class~="primary"]')] == ["Video"]
    assert [node.text for node in document.select('a[lang|="zh"]')] == ["Video"]
    assert extract_fields(
        document.root,
        [FieldRule(name="video_url", type="attr", selector='a[href*="/video/"]', attribute="href")],
    ) == {"video_url": "/video/BV123"}


def test_children_namespace_and_override():
    html = '<article><div class="seller"><span class="name">Acme</span><span>rating: 5</span></div></article>'
    document = HTMLDocument(html)
    rules = [
        FieldRule(
            name="seller",
            type="css",
            selector=".seller",
            namespace="seller",
            children=[
                FieldRule(name="name", type="css", selector=".name"),
                FieldRule(name="rating", type="regex", pattern=r"rating:\s*(\d+)", transforms=["int"]),
            ],
        ),
        FieldRule(name="seller", type="css", selector=".missing", default="ignored", override=False),
    ]

    assert extract_fields(document.root, rules) == {"seller": {"name": "Acme", "rating": 5}}


def test_required_and_optional_missing_fields():
    document = HTMLDocument("<article><h2>Title</h2></article>")

    optional = extract_value(document.root, FieldRule(name="summary", type="css", selector=".missing", default="n/a"))

    assert optional == "n/a"
    with pytest.raises(RequiredFieldMissingError):
        extract_value(document.root, FieldRule(name="title", type="css", selector=".missing", required=True))


def test_merge_records_supports_override_and_namespace():
    base = {"title": "List title", "price": 1}
    detail = {"title": "Detail title", "body": "Full"}

    assert merge_records(base, detail, override=False) == {"title": "List title", "price": 1, "body": "Full"}
    assert merge_records(base, detail, namespace="detail")["detail"] == detail


def test_iter_records_streams_api_items():
    spider = SpiderConfig(
        id="api",
        name="api",
        start_urls=["https://example.test/api"],
        items_json_path="items",
        fields=[FieldRule(name="title", type="json_path", json_path="title")],
    )

    assert list(iter_records('{"items":[{"title":"A"},{"title":"B"}]}', spider)) == [{"title": "A"}, {"title": "B"}]
