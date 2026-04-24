"""Lucknow locality, institution, and community reference data.

Used by the relevance scorer and normalizers to ground-truth whether an
event is genuinely Lucknow-relevant.
"""
from __future__ import annotations

LUCKNOW_LOCALITIES: frozenset[str] = frozenset(
    {
        "gomti nagar",
        "gomtinagar",
        "hazratganj",
        "hazrat ganj",
        "aliganj",
        "ali ganj",
        "indira nagar",
        "indiranagar",
        "ashiyana",
        "alambagh",
        "vibhuti khand",
        "mahanagar",
        "rajajipuram",
        "chowk",
        "aminabad",
        "nishatganj",
        "kanpur road",
        "faizabad road",
        "sector 14",
        "kursi road",
        "jankipuram",
        "sushant golf city",
        "chinhat",
        "telibagh",
    }
)

LUCKNOW_INSTITUTIONS: frozenset[str] = frozenset(
    {
        "iiit lucknow",
        "iiit-l",
        "iiitl",
        "iet lucknow",
        "iet",
        "lucknow university",
        "bbd university",
        "bbdu",
        "integral university",
        "amity lucknow",
        "national pg college",
        "shri ramswaroop",
        "srm lucknow",
        "aktu",
        "dr apj abdul kalam technical university",
        "hbtu",
        "sam higginbottom university",
        "era university",
    }
)

LUCKNOW_COMMUNITIES: frozenset[str] = frozenset(
    {
        "gdg lucknow",
        "gdsc lucknow",
        "wtm lucknow",
        "mlsa lucknow",
        "iiit-l coding club",
        "lucknow developers",
        "up ai labs",
        "mozilla lucknow",
        "owasp lucknow",
        "hackclub lucknow",
    }
)
