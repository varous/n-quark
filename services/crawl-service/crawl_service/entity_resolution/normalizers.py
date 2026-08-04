"""Deterministic, entity-type-specific normalizers (Phase 3.1). Pure, no LLM.

Each normalizer produces a *matching* form used only to compare identities — the raw value is always
preserved separately. Normalization is deliberately conservative: it must not erase a distinction that
could make two genuinely different entities look identical (a tribute act vs the original artist, two
generic "Town Hall" venues in different cities, two series with the same generic title).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

NORMALIZER_VERSION = "entity-normalizer-1"

# ---- shared helpers -----------------------------------------------------------------------------
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def strip_diacritics(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def _base(value: str | None) -> str:
    v = strip_diacritics(value or "").lower()
    v = _PUNCT.sub(" ", v)
    return _WS.sub(" ", v).strip()


def slug(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _base(value)).strip("-")


# ================================================================================ ARTIST
# feat./ft./featuring/with + trailing guest performer (kept as a separate lineup entry upstream).
_FEAT = re.compile(r"\b(feat|ft|featuring|with|w)\b[\.:]?\s+.*$", re.IGNORECASE)
_HONORIFICS = re.compile(r"^(dj|mc|dr|sir|the honourable)\s+", re.IGNORECASE)
_LIVE_TAIL = re.compile(r"\b(live|live in concert|india tour|world tour|tour)\b.*$", re.IGNORECASE)
_TRIBUTE = re.compile(r"\b(tribute|tributes|covers?|cover band|tribute band|a tribute to)\b", re.IGNORECASE)


@dataclass
class ArtistNorm:
    raw: str
    normalized: str
    is_tribute: bool = False
    stripped_feat: bool = False


def normalize_artist(name: str | None) -> ArtistNorm:
    raw = (name or "").strip()
    is_tribute = bool(_TRIBUTE.search(raw))
    work = _FEAT.sub("", raw)
    stripped_feat = work != raw
    work = _LIVE_TAIL.sub("", work)
    work = _HONORIFICS.sub("", work.strip())
    normalized = _base(work)
    # A tribute/cover act keeps the tribute marker in its identity so it can NEVER normalize to the
    # original artist (deliberate: the marker is the distinction that matters).
    if is_tribute and "tribute" not in normalized and "cover" not in normalized:
        normalized = f"{normalized} tribute".strip()
    return ArtistNorm(raw=raw, normalized=normalized, is_tribute=is_tribute, stripped_feat=stripped_feat)


# Common, ambiguous single-token stage names — must never auto-resolve on name alone.
AMBIGUOUS_ARTIST_NAMES = frozenset({
    "king", "raftaar", "anand", "agnee", "the local train", "advait", "when chai met toast",
    "arjun", "raja", "guru", "prince", "queen",
})


def is_ambiguous_artist(norm: ArtistNorm) -> bool:
    if norm.normalized in AMBIGUOUS_ARTIST_NAMES:
        return True
    # single very-short token with no distinguishing context is ambiguous
    return len(norm.normalized) <= 3 or (len(norm.normalized.split()) == 1 and len(norm.normalized) <= 4)


# ================================================================================ VENUE
_THE_PREFIX = re.compile(r"^the\s+", re.IGNORECASE)
_VENUE_ABBR = {
    r"\baud\b": "auditorium",
    r"\bhall\b": "hall",
    r"\bstdm\b": "stadium",
    r"\bgrnd\b": "ground",
}
# Generic venue names that MUST NOT collapse across locations without geography evidence.
GENERIC_VENUE_NAMES = frozenset({
    "town hall", "community hall", "the club", "club", "auditorium", "open air theatre",
    "open air theater", "amphitheatre", "amphitheater", "convention centre", "convention center",
    "banquet hall", "community centre", "community center", "city hall", "multipurpose hall",
})


@dataclass
class VenueNorm:
    raw: str
    normalized: str
    is_generic: bool = False


def normalize_venue(name: str | None) -> VenueNorm:
    raw = (name or "").strip()
    work = _THE_PREFIX.sub("", _base(raw))
    for pat, repl in _VENUE_ABBR.items():
        work = re.sub(pat, repl, work)
    normalized = _WS.sub(" ", work).strip()
    return VenueNorm(raw=raw, normalized=normalized, is_generic=normalized in GENERIC_VENUE_NAMES)


# ================================================================================ ORGANIZER
# Legal / commercial suffixes stripped for *matching* only (raw retained by the caller).
_ORG_SUFFIXES = re.compile(
    r"\b(pvt\.?\s*ltd\.?|private\s+limited|ltd\.?|llp|inc\.?|productions?|"
    r"events?|entertainment|media|group|company|co\.?)\b",
    re.IGNORECASE,
)


@dataclass
class OrganizerNorm:
    raw: str
    normalized: str


def normalize_organizer(name: str | None) -> OrganizerNorm:
    raw = (name or "").strip()
    work = _ORG_SUFFIXES.sub(" ", _base(raw))
    return OrganizerNorm(raw=raw, normalized=_WS.sub(" ", work).strip() or _base(raw))


# ================================================================================ EVENT SERIES
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
          "xi": 11, "xii": 12, "xiii": 13}
_EDITION_NUM = re.compile(
    r"\b(?:edition|ed|vol|volume|chapter|ch|season|part|pt|#)\s*[:\-]?\s*(\d{1,3})\b", re.IGNORECASE)
_EDITION_ROMAN = re.compile(
    r"\b(?:edition|vol|volume|chapter|season|part)\s+([ivx]{1,4})\b", re.IGNORECASE)
_TRAILING_ROMAN = re.compile(r"\s+([ivx]{2,4})$", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")
_PRESENTED_BY = re.compile(r"^.*?\bpresents?\b[:\-]?\s*", re.IGNORECASE)
_EDITION_MARKERS = re.compile(
    r"\b(edition|ed|vol|volume|chapter|ch|season|part|pt)\b\s*[:\-]?\s*[\divx]{0,4}", re.IGNORECASE)

# Generic recurring titles that must NOT link into a series without stronger identity evidence.
GENERIC_SERIES_TITLES = frozenset({
    "saturday night", "open mic", "live music", "comedy night", "karaoke night", "ladies night",
    "friday night", "sunday brunch", "quiz night", "jam night", "trivia night", "happy hours",
})


@dataclass
class SeriesNorm:
    raw: str
    series_normalized: str          # identity of the recurring property
    edition_label: str | None = None  # e.g. "Edition 2", "2026", "VIII"
    edition_number: int | None = None
    is_generic: bool = False
    markers: list[str] = field(default_factory=list)


def normalize_series(title: str | None) -> SeriesNorm:
    raw = (title or "").strip()
    markers: list[str] = []
    edition_number: int | None = None
    edition_label: str | None = None

    m = _EDITION_NUM.search(raw)
    if m:
        edition_number = int(m.group(1))
        edition_label = m.group(0).strip()
        markers.append("edition_number")
    if edition_number is None:
        mr = _EDITION_ROMAN.search(raw) or _TRAILING_ROMAN.search(raw)
        if mr:
            token = mr.group(1).lower()
            if token in _ROMAN:
                edition_number = _ROMAN[token]
                edition_label = mr.group(0).strip()
                markers.append("edition_roman")
    y = _YEAR.search(raw)
    if y:
        markers.append("year")
        if edition_label is None:
            edition_label = y.group(1)

    work = _PRESENTED_BY.sub("", raw)
    work = _EDITION_MARKERS.sub(" ", work)
    work = _TRAILING_ROMAN.sub(" ", work)
    work = _YEAR.sub(" ", work)
    series_normalized = _base(work)

    return SeriesNorm(
        raw=raw, series_normalized=series_normalized, edition_label=edition_label,
        edition_number=edition_number, is_generic=series_normalized in GENERIC_SERIES_TITLES,
        markers=markers,
    )
