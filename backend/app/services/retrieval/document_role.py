from __future__ import annotations

VALID_DOCUMENT_ROLES = frozenset({"tender", "bid", "other"})


def resolve_document_role(
    *,
    file_id: str,
    tender_file_id: str | None,
    bid_file_id: str | None,
    stored_role: str | None = None,
) -> str:
    if stored_role in VALID_DOCUMENT_ROLES:
        return stored_role
    if tender_file_id and file_id == tender_file_id:
        return "tender"
    if bid_file_id and file_id == bid_file_id:
        return "bid"
    return "other"
