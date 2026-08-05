"""Pure logic: identity, header metadata, and the creative-transition detector."""

import struct
from datetime import UTC, datetime, timedelta

from media_service import identity, metadata
from media_service import transitions as T
from tests.conftest import png_bytes

T0 = datetime(2026, 8, 5, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)


# ---- identity --------------------------------------------------------------------------------
def test_sha256_is_content_based():
    a, b = png_bytes(1, 1, b"x"), png_bytes(1, 1, b"x")
    assert identity.content_sha256(a) == identity.content_sha256(b)
    assert identity.content_sha256(a) != identity.content_sha256(png_bytes(1, 1, b"y"))


def test_normalize_url_drops_fragment_and_default_port_lowercases_host():
    assert identity.normalize_url("HTTPS://CDN.Example.com:443/a/b.jpg#frag") == "https://cdn.example.com/a/b.jpg"
    assert identity.normalize_url("http://h.com:80/x?q=1") == "http://h.com/x?q=1"


def test_identity_key_prefers_content_then_url():
    assert identity.identity_key("sha", "http://x/a.jpg") == "sha"
    assert identity.identity_key(None, "http://x/a.jpg") == "url:http://x/a.jpg"


# ---- metadata --------------------------------------------------------------------------------
def test_png_dimensions():
    m = metadata.extract(png_bytes(1024, 768))
    assert m.mime_type == "image/png" and m.width == 1024 and m.height == 768
    assert m.aspect_ratio == round(1024 / 768, 4)


def test_gif_and_non_image():
    gif = b"GIF89a" + struct.pack("<HH", 320, 240) + b"\x00" * 4
    m = metadata.extract(gif)
    assert m.image_format == "GIF" and (m.width, m.height) == (320, 240)
    assert metadata.extract(b"not an image") is None


# ---- transitions -----------------------------------------------------------------------------
def _present(asset_id, url, at, authoritative=False):
    return T.ObsFacts(kind="PRESENT", media_asset_id=asset_id, normalized_url=url, observed_at=at)


def test_first_seen():
    d = T.detect(None, _present("shaA", "u1", T0))
    assert d.transitions == [T.MEDIA_FIRST_SEEN] and d.new_state.present and d.new_state.version == 1


def _state_after(prev, obs):
    return T.detect(prev, obs)


def test_same_bytes_two_urls_is_url_change_not_content_change():
    first = T.detect(None, _present("shaA", "u1", T0)).new_state
    d = T.detect(first, _present("shaA", "u2", T1))
    assert d.transitions == [T.MEDIA_URL_CHANGED_SAME_CONTENT]
    assert d.new_state.version == first.version  # not a creative change


def test_changed_bytes_same_url_is_content_change():
    first = T.detect(None, _present("shaA", "u1", T0)).new_state
    d = T.detect(first, _present("shaB", "u1", T1))
    assert d.transitions == [T.MEDIA_CONTENT_CHANGED] and d.new_state.version == 2


def test_idempotent_repeat_observation():
    first = T.detect(None, _present("shaA", "u1", T0)).new_state
    d = T.detect(first, _present("shaA", "u1", T1))
    assert d.transitions == [] and not d.changed


def test_out_of_order_never_rewrites_state():
    first = T.detect(None, _present("shaA", "u1", T1)).new_state
    d = T.detect(first, _present("shaB", "u9", T0))  # earlier than last_observed
    assert d.out_of_order and d.new_state is None and d.transitions == []


def test_failed_fetch_is_not_disappearance():
    first = T.detect(None, _present("shaA", "u1", T0)).new_state
    d = T.detect(first, T.ObsFacts(kind="ABSENT", media_asset_id=None, normalized_url="",
                                   observed_at=T1, authoritative_absence=False))
    assert d.transitions == [] and d.new_state is None


def test_authoritative_disappearance_then_reappearance():
    first = T.detect(None, _present("shaA", "u1", T0)).new_state
    gone = T.detect(first, T.ObsFacts(kind="ABSENT", media_asset_id=None, normalized_url="",
                                      observed_at=T1, authoritative_absence=True))
    assert gone.transitions == [T.MEDIA_DISAPPEARED] and gone.new_state.present is False
    back = T.detect(gone.new_state, _present("shaC", "u3", T2))
    assert T.MEDIA_REAPPEARED in back.transitions and T.MEDIA_CONTENT_CHANGED in back.transitions
    assert back.new_state.present is True
