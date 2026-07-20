from __future__ import annotations

import pytest

from app.services.retrieval.document_role import resolve_document_role


@pytest.mark.parametrize(
    ("stored_role", "expected"),
    [
        ("tender", "tender"),
        ("bid", "bid"),
        ("other", "other"),
    ],
)
def test_resolve_document_role_prefers_stored(stored_role, expected):
    assert (
        resolve_document_role(
            file_id="any",
            tender_file_id="t1",
            bid_file_id="b1",
            stored_role=stored_role,
        )
        == expected
    )


def test_resolve_document_role_infers_from_tender_file_id():
    assert (
        resolve_document_role(
            file_id="t1",
            tender_file_id="t1",
            bid_file_id="b1",
            stored_role=None,
        )
        == "tender"
    )


def test_resolve_document_role_infers_from_bid_file_id():
    assert (
        resolve_document_role(
            file_id="b1",
            tender_file_id="t1",
            bid_file_id="b1",
            stored_role=None,
        )
        == "bid"
    )


def test_resolve_document_role_other_when_no_match():
    assert (
        resolve_document_role(
            file_id="x1",
            tender_file_id="t1",
            bid_file_id="b1",
            stored_role=None,
        )
        == "other"
    )


def test_resolve_document_role_ignores_invalid_stored():
    assert (
        resolve_document_role(
            file_id="t1",
            tender_file_id="t1",
            bid_file_id="b1",
            stored_role="invalid",
        )
        == "tender"
    )
