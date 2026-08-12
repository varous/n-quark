"""Parse an operator-pasted YouTube hint into a typed reference (Phase 5B.1).

Pure string parsing only — it classifies what the operator pasted (a channel id, a @handle, or a
video id) so the resolver can pick the right *authoritative* provider call. Parsing a URL is NEVER
verification: a syntactically valid hint still has to clear channels.list / videos.list before it can
resolve an identity. See ``service.resolve_youtube_from_hint``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# A YouTube channel id is "UC" + 22 url-safe base64 chars.
_CHANNEL_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
# A video id is 11 url-safe base64 chars.
_VIDEO_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9._-]{2,100}$")

CHANNEL_ID = "CHANNEL_ID"
HANDLE = "HANDLE"
VIDEO_ID = "VIDEO_ID"


@dataclass(frozen=True)
class YouTubeRef:
    kind: str          # CHANNEL_ID | HANDLE | VIDEO_ID
    value: str         # UC… | handle (no leading @) | 11-char video id
    raw: str           # exactly what the operator pasted


def parse_youtube_hint(raw: str | None) -> YouTubeRef | None:
    """Classify a pasted hint. Returns None when it is not recognisably a YouTube reference.

    Accepts full URLs (youtube.com / youtu.be / m.youtube.com), bare channel ids (UC…), bare @handles,
    and bare 11-char video ids. Handles are lower-cased and stripped of the leading ``@``."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    # bare forms (no scheme / host)
    if _CHANNEL_RE.match(s):
        return YouTubeRef(CHANNEL_ID, s, raw)
    if s.startswith("@") and _HANDLE_RE.match(s):
        return YouTubeRef(HANDLE, s[1:].lower(), raw)

    parsed = urlparse(s if "//" in s else "https://" + s)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = parsed.path or ""
    segments = [seg for seg in path.split("/") if seg]

    if host in ("youtu.be",) and segments:
        vid = segments[0]
        return YouTubeRef(VIDEO_ID, vid, raw) if _VIDEO_RE.match(vid) else None

    if host in ("youtube.com", "music.youtube.com"):
        # /watch?v=VIDEO
        if segments and segments[0] == "watch":
            v = parse_qs(parsed.query).get("v", [None])[0]
            return YouTubeRef(VIDEO_ID, v, raw) if v and _VIDEO_RE.match(v) else None
        # /shorts/VIDEO, /embed/VIDEO, /live/VIDEO
        if len(segments) >= 2 and segments[0] in ("shorts", "embed", "live"):
            vid = segments[1]
            return YouTubeRef(VIDEO_ID, vid, raw) if _VIDEO_RE.match(vid) else None
        # /channel/UC…
        if len(segments) >= 2 and segments[0] == "channel":
            cid = segments[1]
            return YouTubeRef(CHANNEL_ID, cid, raw) if _CHANNEL_RE.match(cid) else None
        # /@handle
        if segments and segments[0].startswith("@"):
            handle = segments[0][1:]
            return YouTubeRef(HANDLE, handle.lower(), raw) if _HANDLE_RE.match("@" + handle) else None
        # /c/NAME or /user/NAME (legacy custom URLs → treat the trailing segment as a handle)
        if len(segments) >= 2 and segments[0] in ("c", "user"):
            handle = segments[1]
            return YouTubeRef(HANDLE, handle.lower(), raw) if _HANDLE_RE.match(handle) else None

    # a bare handle without @ but clearly not a URL (single token) — treat as handle only if it is not
    # accidentally an 11/24-char id (handled above) and contains no spaces.
    if "/" not in s and "." not in s and " " not in s and _HANDLE_RE.match(s):
        return YouTubeRef(HANDLE, s.lower(), raw)

    return None
