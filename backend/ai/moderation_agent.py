from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from ai.gemini_client import get_client, json_config
from api.core.config import settings


@dataclass(slots=True)
class ModerationInput:
    submitter_name: str | None
    submitter_email: str | None
    event_url: str
    notes: str | None
    poster_text: str | None = None


class GeminiModerationOutput(BaseModel):
    decision: str
    reason: str
    spam_likelihood: float
    tech_relevance: float


SYSTEM_PROMPT = """You triage manual event submissions for a Lucknow, India tech events aggregator.

CONTEXT: Lucknow, UP, India has approximately 60–80 tech events per year.
Active tech communities in Lucknow include:
- GDG Lucknow (Google Developer Groups)
- TFUG Lucknow (TensorFlow User Group / AI community)
- FOSS United Lucknow (open source)
- AWS User Group Lucknow
- Lucknow AI Labs
- Cloud Native Lucknow (CNCF chapter)
- GDG on Campus chapters (IIIT Lucknow, SRMCEM, BBDNIIT, BNCET, Integral University)
- College fests: HackoFiesta (IIIT Lucknow), AXIOS (IIIT Lucknow), E-Summit

Your task: Evaluate whether a submitted URL is a real, upcoming tech event relevant to Lucknow.

Return JSON with exactly these fields:
{
  "decision": "approve" | "reject" | "human_review",
  "reason": "<brief explanation>",
  "spam_likelihood": 0.0-1.0,
  "tech_relevance": 0.0-1.0
}

DECISION CRITERIA:
- "approve": Clearly a real tech event in/relevant to Lucknow. URL looks like a specific event page.
  spam_likelihood < 0.2 AND tech_relevance > 0.7
- "reject": Obviously spam, non-tech content, unrelated to Lucknow/UP, clearly expired event,
  or a generic listing/directory page (not a specific event).
  spam_likelihood > 0.7 OR tech_relevance < 0.2
- "human_review": Uncertain — could be legitimate but needs admin verification.
  Event may be real but Lucknow relevance is unclear, or the URL is ambiguous.

NOTE: Be inclusive for Lucknow — if the event is from a known Lucknow community or the URL
mentions Lucknow, lean toward approve or human_review rather than reject."""


async def triage_submission(inp: ModerationInput) -> GeminiModerationOutput:
    client = get_client()
    model = settings.GEMINI_MODEL
    payload = {
        "submitter_name": inp.submitter_name,
        "submitter_email": inp.submitter_email,
        "event_url": inp.event_url,
        "notes": inp.notes,
        "poster_text": inp.poster_text,
    }
    resp = await client.aio.models.generate_content(
        model=model,
        contents=[SYSTEM_PROMPT, str(payload)],
        config=json_config(GeminiModerationOutput),
    )
    if getattr(resp, "parsed", None) is not None:
        return resp.parsed
    return GeminiModerationOutput.model_validate_json(resp.text)
