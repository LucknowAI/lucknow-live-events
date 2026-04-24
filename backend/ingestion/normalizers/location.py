from __future__ import annotations

from ingestion.location_data import LUCKNOW_LOCALITIES


def normalize_city(city: str | None) -> str | None:
    if city is None:
        return None
    city_l = city.lower().strip()
    if "lucknow" in city_l:
        return "Lucknow"
    return city.strip() or None


def normalize_locality(locality: str | None) -> str | None:
    if not locality:
        return None
    loc_l = locality.lower().strip()
    for known in LUCKNOW_LOCALITIES:
        if known in loc_l:
            return locality.strip()
    return locality.strip() or None
