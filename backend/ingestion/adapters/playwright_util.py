"""Shared Playwright utilities for all scrapers.

Key helpers:
  playwright_render(url) – render a page and return its HTML
  playwright_fetch_html(url) – alias for playwright_render
  playwright_intercept_json(url, match_pattern) – capture an XHR/fetch JSON response
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"


# Default timeouts (ms)
_NAVIGATION_TIMEOUT_MS = 60_000
_NETWORK_IDLE_TIMEOUT_MS = 30_000


def _apply_stealth(page: Any) -> None:
    """Apply playwright-stealth if available; silently skip if not installed."""
    try:
        from playwright_stealth import stealth_sync  # type: ignore
        stealth_sync(page)
    except ImportError:
        pass
    except Exception as exc:
        log.debug("playwright_stealth.apply_failed", error=str(exc))


async def _apply_stealth_async(page: Any) -> None:
    """Async version of stealth application."""
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
    except ImportError:
        pass
    except Exception as exc:
        log.debug("playwright_stealth.apply_async_failed", error=str(exc))


async def playwright_render(url: str, *, wait_for: str = "networkidle") -> str:
    """
    Render a URL using Playwright Chromium and return the page HTML.

    Args:
        url: Target URL.
        wait_for: Playwright waitUntil strategy ("networkidle", "load", "domcontentloaded").
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await ctx.new_page()
        await _apply_stealth_async(page)

        try:
            await page.goto(url, wait_until=wait_for, timeout=_NAVIGATION_TIMEOUT_MS)
        except Exception:
            # Fallback: just wait for load if networkidle times out
            try:
                await page.wait_for_load_state("load", timeout=_NETWORK_IDLE_TIMEOUT_MS)
            except Exception:
                pass

        html = await page.content()
        await browser.close()
    return html


# Backwards-compat alias
playwright_fetch_html = playwright_render


async def playwright_intercept_json(
    url: str,
    *,
    match_pattern: str | None = None,
    wait_for_selector: str | None = None,
    scroll_to_bottom: bool = False,
    extra_wait_ms: int = 2000,
) -> tuple[str, dict[str, Any] | list[Any] | None]:
    """
    Navigate to ``url`` and capture the first XHR/fetch JSON response whose URL
    matches ``match_pattern`` (regex). Also returns the final page HTML.

    Returns:
        (page_html, captured_json_or_None)
    """
    import json as _json

    from playwright.async_api import async_playwright

    captured: dict[str, Any] | list[Any] | None = None
    captured_event = asyncio.Event()

    pattern = re.compile(match_pattern) if match_pattern else None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()
        await _apply_stealth_async(page)

        async def on_response(response: Any) -> None:
            nonlocal captured
            if captured_event.is_set():
                return
            resp_url = response.url
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype:
                return
            if pattern and not pattern.search(resp_url):
                return
            try:
                body = await response.json()
                captured = body
                captured_event.set()
            except Exception:
                pass

        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS)
        except Exception:
            pass

        if wait_for_selector:
            try:
                await page.wait_for_selector(wait_for_selector, timeout=10_000)
            except Exception:
                pass

        if scroll_to_bottom:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

        # Wait up to extra_wait_ms for the JSON response to be intercepted
        try:
            await asyncio.wait_for(captured_event.wait(), timeout=extra_wait_ms / 1000)
        except asyncio.TimeoutError:
            pass

        html = await page.content()
        await browser.close()

    return html, captured
