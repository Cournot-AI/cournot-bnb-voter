"""Unit tests for erc8183_voter.adapter (query construction)."""

from __future__ import annotations

import pytest

from erc8183_voter.adapter import (
    DeliverableManifest,
    build_verification_query,
    manifest_hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(content: str = "Some deliverable text", content_type: str = "text/plain") -> DeliverableManifest:
    return DeliverableManifest(
        response={"content_type": content_type, "content": content},
        raw=content,
    )


class _SimpleJob:
    def __init__(self, description: str = "Do something"):
        self.description = description


class _StructuredJobDesc:
    def __init__(self):
        self.task = "Generate a logo"
        self.terms = {
            "deliverables": "A PNG logo image",
            "quality_standards": "High resolution",
            "success_criteria": "Matches brief",
        }


# ---------------------------------------------------------------------------
# Tests: structured JobDescription
# ---------------------------------------------------------------------------

class TestBuildVerificationQueryStructured:
    def test_contains_job_task(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=_make_manifest(),
            hash_ok=True,
        )
        assert "Generate a logo" in query

    def test_contains_deliverables(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=_make_manifest(),
            hash_ok=True,
        )
        assert "A PNG logo image" in query

    def test_contains_quality_standards(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=_make_manifest(),
            hash_ok=True,
        )
        assert "High resolution" in query

    def test_contains_success_criteria(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=_make_manifest(),
            hash_ok=True,
        )
        assert "Matches brief" in query

    def test_hash_ok_true(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=_make_manifest(),
            hash_ok=True,
        )
        assert "Manifest hash matches on-chain: True" in query

    def test_hash_ok_false(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=_make_manifest(),
            hash_ok=False,
        )
        assert "Manifest hash matches on-chain: False" in query


# ---------------------------------------------------------------------------
# Tests: plain-text fallback
# ---------------------------------------------------------------------------

class TestBuildVerificationQueryPlainText:
    def test_fallback_uses_job_description(self):
        job = _SimpleJob(description="Please write a poem about cats")
        query = build_verification_query(
            job=job,
            job_desc=None,
            manifest=_make_manifest("Here is a poem about cats"),
            hash_ok=True,
        )
        assert "Please write a poem about cats" in query
        assert "Here is a poem about cats" in query


# ---------------------------------------------------------------------------
# Tests: missing manifest
# ---------------------------------------------------------------------------

class TestBuildVerificationQueryNoManifest:
    def test_manifest_unavailable_message(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=None,
            hash_ok=False,
        )
        assert "(manifest unavailable)" in query

    def test_still_has_job_spec(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=_StructuredJobDesc(),
            manifest=None,
            hash_ok=False,
        )
        assert "Generate a logo" in query


# ---------------------------------------------------------------------------
# Tests: manifest_hash
# ---------------------------------------------------------------------------

class TestManifestHash:
    def test_returns_hex_prefixed(self):
        m = _make_manifest("hello")
        h = manifest_hash(m)
        assert h.startswith("0x")
        assert len(h) == 66  # 0x + 64 hex chars

    def test_deterministic(self):
        m1 = _make_manifest("same content")
        m2 = _make_manifest("same content")
        assert manifest_hash(m1) == manifest_hash(m2)

    def test_different_content_different_hash(self):
        m1 = _make_manifest("content A")
        m2 = _make_manifest("content B")
        assert manifest_hash(m1) != manifest_hash(m2)


# ---------------------------------------------------------------------------
# Tests: answer instructions present
# ---------------------------------------------------------------------------

class TestQueryInstructions:
    def test_yes_no_invalid_instructions(self):
        query = build_verification_query(
            job=_SimpleJob(),
            job_desc=None,
            manifest=_make_manifest(),
            hash_ok=True,
        )
        assert "Answer YES" in query
        assert "Answer NO" in query
        assert "Answer INVALID" in query
