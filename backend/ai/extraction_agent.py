from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from ai.gemini_client import get_client, json_config
from api.core.config import settings


@dataclass(slots=True)
class ExtractionInput:
    source_platform: str
    source_url: str
    page_url: str
    cleaned_text: str
    partial_hints: dict


class GeminiExtractionOutput(BaseModel):
    title: str | None = None
    description: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    timezone: str = "Asia/Kolkata"
    city: str | None = None
    locality: str | None = None
    venue_name: str | None = None
    address: str | None = None
    mode: str | None = None
    event_type: str | None = None
    topics: list[str] = Field(default_factory=list, max_length=5)
    audience: list[str] = Field(default_factory=list)
    organizer_name: str | None = None
    community_name: str | None = None
    registration_url: str | None = None
    price_type: str = "unknown"
    is_free: bool = True
    is_student_friendly: bool = False
    confidence: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)
    not_an_event: bool = False


SYSTEM_PROMPT = """You are a precise structured-data extraction agent for a Lucknow, India tech events aggregator.

Your SOLE task: Extract ONE tech event from the provided webpage text into strict JSON.

CRITICAL RULES:

1. DATES & TIMES are the #1 priority. You MUST extract the exact start date, start time, end date, and end time.
   - Format start_at and end_at as ISO 8601 strings with timezone offset, e.g. "2026-05-15T10:00:00+05:30".
   - Scan all text for date patterns: "15 May 2026", "May 15, 2026", "15/05/2026", "2026-05-15", "15th May", "April 30".
   - Scan for time patterns: "10:00 AM IST", "10:00 AM", "10 AM", "starts at 10", "10:00 - 14:00", "6 PM".
   - If only a date is found without a time, set time to 10:00:00+05:30 (common Lucknow event start) and add "time" to missing_fields.
   - If a date range like "May 15-17" is found: start_at = first day at 10:00, end_at = last day at 18:00.
   - All times should be in +05:30 (IST) unless another timezone is explicitly stated.
   - NEVER fabricate dates. If no date is present in the text, set start_at = null.

2. LOCATION: Set city = "Lucknow" ONLY if clearly stated or if the venue/organizer is known to be in Lucknow, UP, India.
   - Do not assume Lucknow — only set it when the text explicitly indicates it.
   - Extract venue_name (e.g. "IIIT Lucknow Auditorium"), locality (e.g. "Gomti Nagar"), and address if present.
   - mode must be one of: "offline", "online", or "hybrid". Look for keywords like "in-person", "virtual", "zoom", "webinar", "at venue".

3. REGISTRATION URL: Prefer the most direct registration/RSVP/ticket link. If none found, use the page URL.

4. EVENT DETECTION: Set not_an_event=true if the page is clearly NOT a single tech event:
   - Blog posts, news articles, product landing pages, job listings, listing/directory pages → not_an_event=true.
   - If it describes one specific, named, scheduled tech event → keep not_an_event=false.

5. PRICE: Look for "Free", "No fee", "₹0", "Paid", "₹", "Registration fee", "Entry fee".
   - is_free=true if free/no-cost. is_free=false if paid. Default is_free=true if completely unclear.
   - price_type: "free", "paid", or "unknown".

6. CONFIDENCE: Score 0.0–1.0:
   - 0.90+ : title + exact start date & time + venue/mode extracted
   - 0.70–0.89: title + start date (no time) + venue/mode
   - 0.50–0.69: title + start date only
   - 0.30–0.49: title only (date missing)
   - 0.00: not_an_event=true

7. MISSING_FIELDS: List field names you could NOT find: e.g. ["end_at", "venue_name", "time", "description"].

8. NEVER invent or hallucinate data. Return null for any field not present in the text.
   Do not use placeholder values like "TBD", "Unknown", or "N/A" — just return null."""


GROUNDED_SYSTEM_PROMPT = """You are a precise event data extraction agent for a Lucknow, India tech events aggregator.

Using Google Search, look up the exact event at the given URL and extract all event details.
Focus especially on: exact date, exact time (IST), venue in Lucknow, registration link, and price.

Rules:
- Format start_at and end_at as ISO 8601 with +05:30 timezone offset.
- Set city = "Lucknow" only if the event is in Lucknow, UP, India.
- If no date is found even after searching, set start_at = null.
- Return null for any field you genuinely cannot find.
- NEVER fabricate data."""


# ─── Pre-LLM garbage filter ───────────────────────────────────────────────────

_EVENT_VOCAB = {
    "event", "workshop", "hackathon", "conference", "meetup", "webinar",
    "register", "rsvp", "tickets", "join us", "session", "talk", "speaker",
    "seminar", "summit", "fest", "competition", "contest", "bootcamp",
    "agenda", "schedule", "venue", "attend", "participate",
}

_JS_NOISE_PATTERNS = (
    "window.__", "@font-face", "__NEXT_DATA__", "webpackJsonp",
    "function(", "var ", "const ", ".css(", "document.cookie",
    "getElementsByTagName", "addEventListener", "querySelector",
)

_ERROR_SIGNALS = (
    "page not found", "404 not found", "access denied",
    "robot check", "enable javascript", "just a moment",
    "please enable cookies", "checking your browser",
)


def _is_garbage(text: str) -> tuple[bool, str]:
    """
    Quick pre-LLM check. Returns (True, reason) if the page content is
    clearly not worth sending to Gemini.
    """
    stripped = text.strip()

    # 1. Too short — 404, redirect, or empty scrape
    if len(stripped) < 200:
        return True, "too_short"

    lower = stripped.lower()

    # 2. Known error page signals dominate the content
    if any(sig in lower for sig in _ERROR_SIGNALS):
        # Only flag if error signal appears near the top (first 500 chars)
        top = lower[:500]
        if any(sig in top for sig in _ERROR_SIGNALS):
            return True, "error_page"

    # 3. JS/CSS soup — high noise, no useful text
    noise_hits = sum(1 for pat in _JS_NOISE_PATTERNS if pat in stripped)
    total_chars = len(stripped)
    # Rough heuristic: if >4 noise markers AND fewer than 300 non-whitespace
    # alphabetic chars it's overwhelmingly JS boilerplate
    alpha_chars = sum(1 for c in stripped if c.isalpha())
    if noise_hits >= 4 and alpha_chars < 300:
        return True, "js_soup"

    # 4. No event vocabulary signal at all
    words = set(lower.split())
    has_event_word = bool(words & _EVENT_VOCAB)
    # Allow short pages through if they have any event keyword
    if not has_event_word and total_chars < 600:
        return True, "no_event_signal"

    return False, ""


async def extract_event(inp: ExtractionInput) -> GeminiExtractionOutput:
    if settings.AI_MODE.lower() == "mock":
        return _mock_extract(inp)

    # ── Pre-LLM garbage filter ────────────────────────────────────────────────
    import structlog as _sl
    _log = _sl.get_logger(__name__)

    garbage, reason = _is_garbage(inp.cleaned_text)
    if garbage:
        _log.info(
            "extraction.prefilter_skip",
            url=inp.page_url,
            reason=reason,
            text_len=len(inp.cleaned_text),
        )
        return GeminiExtractionOutput(not_an_event=True, confidence=0.0)

    client = get_client()
    model = settings.GEMINI_MODEL

    # Heuristic: detect JS-heavy SPA pages that have an event signal but no
    # date signal — worth trying grounded search rather than raw text.
    import re
    text_lower = inp.cleaned_text.lower()
    _DATE_KEYWORDS = (
        "date", "time", "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "2025", "2026", "2027", "starts", "ends", "register by", "am", "pm",
        "morning", "evening", "afternoon",
    )
    has_date_signal = any(kw in text_lower for kw in _DATE_KEYWORDS)
    # Only trigger grounded fallback on longer pages; short pages are already
    # filtered above — if we reach here the page has meaningful event content.
    is_js_soup = (
        not has_date_signal
        and len(inp.cleaned_text) > 2000
        and (
            "window.__" in inp.cleaned_text
            or "@font-face" in inp.cleaned_text
        )
    )


    user_prompt = (
        f"Source platform: {inp.source_platform}\n"
        f"Page URL: {inp.page_url}\n"
        f"Partial data already known: {inp.partial_hints}\n\n"
        f"--- PAGE TEXT START ---\n{inp.cleaned_text}\n--- PAGE TEXT END ---\n\n"
        "Extract the event data as JSON. Pay special attention to any dates and times mentioned."
    )

    if is_js_soup:
        # Scraped text is mostly JS boilerplate — use Google Search Grounding to
        # retrieve accurate event details (especially the real date/time).
        from google.genai import types
        grounded_prompt = (
            f"Find complete event details for this specific event page: {inp.page_url}\n\n"
            f"Known event title: {inp.partial_hints.get('title', 'unknown')}\n"
            f"Source platform: {inp.source_platform}\n\n"
            "Search for and extract the following EXACTLY:\n"
            "1. Event start date and start time (in IST / Asia/Kolkata timezone)\n"
            "2. Event end date and end time (if available)\n"
            "3. Venue name and full address in Lucknow (if offline/hybrid)\n"
            "4. Registration or RSVP URL\n"
            "5. Whether the event is free or paid (price if paid)\n"
            "6. A 2-3 sentence description of the event\n"
            "7. Organizer name or community name\n\n"
            "Return the event data as JSON matching the provided schema. "
            "If a field is not found even after searching, return null for that field."
        )
        try:
            resp = await client.aio.models.generate_content(
                model=model,
                contents=grounded_prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    response_mime_type="application/json",
                    response_json_schema=GeminiExtractionOutput.model_json_schema(),
                    system_instruction=GROUNDED_SYSTEM_PROMPT,
                    temperature=0.1,
                ),
            )
            result = GeminiExtractionOutput.model_validate_json(resp.text)
            # Only use the grounded result if it produced a real date
            if result.start_at and not result.not_an_event:
                return result
            # Fall through to standard extraction if grounding didn't help
        except Exception as exc:
            import structlog
            structlog.get_logger(__name__).warning("extraction.grounding_fallback_failed", error=str(exc))

    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=user_prompt,
            config=json_config(GeminiExtractionOutput, system_instruction=SYSTEM_PROMPT),
        )
        parsed = getattr(resp, "parsed", None)
        if parsed is not None:
            result = (
                parsed
                if isinstance(parsed, GeminiExtractionOutput)
                else GeminiExtractionOutput.model_validate(parsed)
            )
        else:
            result = GeminiExtractionOutput.model_validate_json(resp.text)

        # ── Grounded date fallback ─────────────────────────────────────────────
        # If the main extraction got a real event but couldn't find the date,
        # fire a targeted grounded search to find just the date/time.
        if result.start_at is None and not result.not_an_event and result.confidence >= 0.30:
            try:
                date_result = await grounded_date_search(
                    page_url=inp.page_url,
                    known_title=result.title,
                )
                if date_result.get("start_at"):
                    result.start_at = date_result["start_at"]
                    result.end_at = date_result.get("end_at") or result.end_at
                    result.confidence = max(result.confidence, 0.65)
                    import structlog as _sl
                    _sl.get_logger(__name__).info(
                        "extraction.grounded_date_filled",
                        url=inp.page_url,
                        start_at=result.start_at,
                    )
            except Exception as _de:
                import structlog as _sl
                _sl.get_logger(__name__).debug("extraction.grounded_date_failed", error=str(_de))

        return result

    except Exception:
        if settings.AI_FALLBACK_TO_MOCK:
            return _mock_extract(inp)
        raise


async def grounded_date_search(page_url: str, known_title: str | None) -> dict:
    """
    Targeted grounded search to find the start/end date of an event whose
    page content didn't contain parseable date information.

    Returns a dict with keys: start_at (ISO str | None), end_at (ISO str | None).
    This is intentionally cheap — it only asks Gemini for 2 fields.
    """
    if settings.AI_MODE.lower() == "mock":
        return {"start_at": None, "end_at": None}

    from pydantic import BaseModel as _BM
    from google.genai import types

    class _DateOnly(_BM):
        start_at: str | None = None
        end_at: str | None = None

    client = get_client()
    prompt = (
        f"Find the exact date and time for this event.\n"
        f"Event URL: {page_url}\n"
        f"Event title (if known): {known_title or 'unknown'}\n\n"
        "Search the web for this specific event and return:\n"
        "- start_at: ISO 8601 datetime with +05:30 offset (e.g. 2026-05-15T10:00:00+05:30)\n"
        "- end_at: ISO 8601 datetime with +05:30 offset, or null if not found\n\n"
        "If you cannot find the date for this event even after searching, return null for both fields."
    )
    system = (
        "You are a date-lookup agent. Use Google Search to find the exact date and time of the given event. "
        "Return ONLY start_at and end_at as ISO 8601 strings with +05:30 timezone. "
        "Never fabricate dates. Return null if not found."
    )
    try:
        resp = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json",
                response_json_schema=_DateOnly.model_json_schema(),
                system_instruction=system,
                temperature=0.0,
            ),
        )
        result = _DateOnly.model_validate_json(resp.text)
        return {"start_at": result.start_at, "end_at": result.end_at}
    except Exception:
        return {"start_at": None, "end_at": None}




def _mock_extract(inp: ExtractionInput) -> GeminiExtractionOutput:
    """
    Heuristic fallback for dev when Gemini is unavailable (quota/network).
    Intentionally conservative: only fills what it can detect.
    """
    import re

    text = inp.cleaned_text or ""
    # Title: look for a labeled title-ish phrase.
    title = None
    m = re.search(r"(?:^|\n)\s*(?:event|title)\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    if m:
        title = m.group(1).strip()[:200]
    if not title:
        for line in text.splitlines():
            s = line.strip()
            if s and len(s) >= 6:
                title = s[:200]
                break

    # URLs
    urls = re.findall(r"https?://\S+", text)
    reg_url = None
    for u in urls:
        if any(k in u.lower() for k in ("register", "rsvp", "tickets", "ticket", "signup")):
            reg_url = u.rstrip(").,]")
            break
    if not reg_url and urls:
        reg_url = urls[0].rstrip(").,]")

    city = "Lucknow" if re.search(r"\blucknow\b", text, re.IGNORECASE) else None

    not_an_event = False
    confidence = 0.35
    if title is None or len(title) < 6:
        not_an_event = True
        confidence = 0.0

    return GeminiExtractionOutput(
        title=title,
        description=text[:800] if text else None,
        start_at=None,
        end_at=None,
        city=city,
        locality=None,
        venue_name=None,
        address=None,
        mode=None,
        event_type=None,
        topics=[],
        audience=[],
        organizer_name=None,
        community_name=None,
        registration_url=reg_url or inp.page_url,
        price_type="unknown",
        is_free=True,
        is_student_friendly=False,
        confidence=confidence,
        missing_fields=[],
        not_an_event=not_an_event,
    )
