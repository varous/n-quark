"""Canonical projection — deterministic folding, dedup, cycle/invalid-chain protection."""

from analytics_service.projection import Canonicalizer, canonicalize


def test_simple_supersession_fold():
    c = canonicalize("venue:legacy", supersession={"venue:legacy": "venue:canonical--kolkata"},
                     identity_states={"venue:canonical--kolkata": "CANONICAL"})
    assert c.canonical_entity_id == "venue:canonical--kolkata"
    assert c.resolution_path == ["venue:legacy", "venue:canonical--kolkata"]
    assert c.identity_state == "CANONICAL"
    assert c.folded and c.warnings == []


def test_multi_hop_chain():
    c = canonicalize("a", supersession={"a": "b", "b": "c"})
    assert c.canonical_entity_id == "c"
    assert c.resolution_path == ["a", "b", "c"]


def test_cycle_is_detected_and_stops():
    c = canonicalize("a", supersession={"a": "b", "b": "a"})
    assert c.canonical_entity_id in {"a", "b"}
    assert any("cycle" in w for w in c.warnings)


def test_self_reference_is_detected():
    c = canonicalize("a", supersession={"a": "a"})
    assert c.canonical_entity_id == "a"
    assert any("self-referential" in w for w in c.warnings)


def test_unknown_target_warns():
    c = canonicalize("a", supersession={"a": "ghost"}, known_ids={"a"})
    assert c.canonical_entity_id == "ghost"
    assert any("not found" in w for w in c.warnings)


def test_alias_fold_after_supersession_precedence():
    # supersession is preferred over alias when both exist for the same node
    c = canonicalize("a", supersession={"a": "canon"}, alias={"a": "other"})
    assert c.canonical_entity_id == "canon"


def test_non_folding_identity():
    c = canonicalize("artist:x", supersession={})
    assert c.canonical_entity_id == "artist:x"
    assert not c.folded


def test_same_name_different_city_stay_distinct():
    # no supersession edge between them → the canonicalizer must NOT merge them
    canon = Canonicalizer(supersession={})
    assert canon.canonical_id("venue:town-hall--kolkata") == "venue:town-hall--kolkata"
    assert canon.canonical_id("venue:town-hall--mumbai") == "venue:town-hall--mumbai"
    assert canon.dedupe(["venue:town-hall--kolkata", "venue:town-hall--mumbai"]) == \
        ["venue:town-hall--kolkata", "venue:town-hall--mumbai"]


def test_dedupe_folds_legacy_and_canonical_to_one():
    canon = Canonicalizer(supersession={"venue:legacy": "venue:canon"})
    out = canon.dedupe(["venue:legacy", "venue:canon", "venue:other"])
    assert out == ["venue:canon", "venue:other"]


def test_superseded_folds_lists_only_folded():
    canon = Canonicalizer(supersession={"venue:legacy": "venue:canon"})
    folds = canon.superseded_folds
    assert [f.input_entity_id for f in folds] == ["venue:legacy"]
    assert folds[0].canonical_entity_id == "venue:canon"
