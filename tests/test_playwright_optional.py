import importlib.util

import pytest


def test_playwright_environment_is_optional():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("Playwright is not installed in this environment.")

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as manager:
            browser = manager.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:
        pytest.skip(f"Playwright browser is not available: {exc}")

