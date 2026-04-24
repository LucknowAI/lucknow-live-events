from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from ai.gemini_client import get_client, json_config
from api.core.config import settings


@dataclass(slots=True)
class ClassificationInput:
    title: str
    description: str | None
    organizer_name: str | None
    community_name: str | None
    source_platform: str
    mode: str | None


class GeminiClassificationOutput(BaseModel):
    event_type: str | None = None
    topics: list[str] = Field(default_factory=list, max_length=5)
    audience: list[str] = Field(default_factory=list)
    is_student_friendly: bool = False
    lucknow_relevance_score: float = 0.5
    confidence: float = 0.0


# Known Lucknow tech communities for relevance scoring
_LUCKNOW_COMMUNITIES = (
    "gdg lucknow", "gdg on campus", "tfug lucknow", "foss united lucknow",
    "aws user group lucknow", "aws cloud club", "lucknow ai labs",
    "cloud native lucknow", "docker lucknow", "hackofiesta", "axios iiit",
    "lucknow developers", "iiit lucknow", "srmcem", "bbdniit", "bncet",
    "integral university", "commudle lucknow", "lucknow tech",
)

SYSTEM_PROMPT = """You enrich already-extracted tech events for a Lucknow, India events aggregator.
Return JSON only. Never invent facts; infer carefully from the provided title, description, and organizer.

RULES:

1. event_type: Choose ONE of:
   "meetup", "hackathon", "workshop", "conference", "webinar", "bootcamp",
   "competition", "fest", "seminar", "panel", or null if truly ambiguous.
   - Hackathons: coding competitions with prizes, usually 24-48h
   - Workshops: hands-on skill-building sessions
   - Meetups: community gatherings, talks, networking
   - Fests: college tech festivals with multiple events

2. topics: Up to 5 specific tech topics from this list (use exact names where possible):
   AI/ML, Generative AI, Web Development, Mobile Development, Cloud Computing,
   DevOps, Open Source, Python, JavaScript, Data Science, Cybersecurity,
   Blockchain, Robotics, IoT, Game Development, UI/UX Design, Entrepreneurship,
   Networking, Career Development, Google Technologies, AWS, Android, Flutter.
   Use shorter forms like "AI/ML", "Web Dev", "Cloud" for readability.

3. audience: Tags from: "developers", "students", "professionals", "beginners",
   "designers", "entrepreneurs", "researchers", "college students", "all levels".

4. lucknow_relevance_score: Float 0.0–1.0 reflecting how likely this event is
   physically in Lucknow, UP, India (or strongly relevant to the Lucknow community):
   - 0.95: Lucknow is explicitly in the title or venue name
   - 0.85: Organizer is a known Lucknow community (GDG Lucknow, TFUG Lucknow,
            AWS UG Lucknow, FOSS United Lucknow, Lucknow AI Labs, IIIT Lucknow,
            HackoFiesta, AXIOS, BBDNIIT, SRMCEM, Integral University, etc.)
   - 0.70: Source platform is commudle/gdg/fossunited with a Lucknow chapter slug
   - 0.50: India-level event with no explicit Lucknow connection
   - 0.20: Clearly from another city (Bangalore, Delhi, Mumbai, etc.)

5. confidence: 0.0–1.0 based on how certain your classification is.
   - 0.9+ if event_type and topics are clearly derivable from the title/description
   - 0.5 if only event_type or only topics could be determined
   - 0.2 if very little information provided"""


async def classify_event(inp: ClassificationInput) -> GeminiClassificationOutput:
    if settings.AI_MODE.lower() == "mock":
        return _mock_classify(inp)

    client = get_client()
    model = settings.GEMINI_MODEL

    # Boost relevance heuristic pre-check so the model has context
    community_lower = (inp.community_name or "").lower()
    organizer_lower = (inp.organizer_name or "").lower()
    known_lucknow = any(
        kw in community_lower or kw in organizer_lower
        for kw in _LUCKNOW_COMMUNITIES
    )

    user_prompt = {
        "title": inp.title,
        "description": inp.description,
        "organizer_name": inp.organizer_name,
        "community_name": inp.community_name,
        "source_platform": inp.source_platform,
        "mode": inp.mode,
        "hint_known_lucknow_community": known_lucknow,
    }
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=str(user_prompt),
            config=json_config(GeminiClassificationOutput, system_instruction=SYSTEM_PROMPT),
        )
        parsed = getattr(resp, "parsed", None)
        if parsed is not None:
            return (
                parsed
                if isinstance(parsed, GeminiClassificationOutput)
                else GeminiClassificationOutput.model_validate(parsed)
            )
        return GeminiClassificationOutput.model_validate_json(resp.text)
    except Exception:
        if settings.AI_FALLBACK_TO_MOCK:
            return _mock_classify(inp)
        raise


def _mock_classify(inp: ClassificationInput) -> GeminiClassificationOutput:
    import re

    text = (inp.title or "") + "\n" + (inp.description or "")
    topics: list[str] = []
    if re.search(r"\bpython\b", text, re.IGNORECASE):
        topics.append("Python")
    if re.search(r"\bjavascript\b|\bjs\b|\breact\b|\bnode\b", text, re.IGNORECASE):
        topics.append("Web Development")
    if re.search(r"\bai\b|\bml\b|machine learning|generative|llm\b", text, re.IGNORECASE):
        topics.append("AI/ML")
    if re.search(r"\bcloud\b|\baws\b|\bgcp\b|\bazure\b", text, re.IGNORECASE):
        topics.append("Cloud Computing")
    if re.search(r"\bopen.?source\b|\bfoss\b", text, re.IGNORECASE):
        topics.append("Open Source")

    event_type = "meetup"
    if re.search(r"hackathon", text, re.IGNORECASE):
        event_type = "hackathon"
    elif re.search(r"workshop", text, re.IGNORECASE):
        event_type = "workshop"
    elif re.search(r"conference|summit|conf\b", text, re.IGNORECASE):
        event_type = "conference"
    elif re.search(r"webinar|online session", text, re.IGNORECASE):
        event_type = "webinar"
    elif re.search(r"fest|festival", text, re.IGNORECASE):
        event_type = "fest"

    # Check for known Lucknow communities
    community_lower = (inp.community_name or "").lower()
    organizer_lower = (inp.organizer_name or "").lower()
    known_lucknow = any(
        kw in community_lower or kw in organizer_lower
        for kw in _LUCKNOW_COMMUNITIES
    )
    lucknow_in_text = bool(re.search(r"\blucknow\b", text, re.IGNORECASE))

    if lucknow_in_text:
        lucknow_score = 0.95
    elif known_lucknow:
        lucknow_score = 0.85
    else:
        lucknow_score = 0.4

    return GeminiClassificationOutput(
        event_type=event_type,
        topics=topics[:5],
        audience=["developers", "students"] if re.search(r"student", text, re.IGNORECASE) else ["developers"],
        is_student_friendly=False,
        lucknow_relevance_score=lucknow_score,
        confidence=0.4,
    )
