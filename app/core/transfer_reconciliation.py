"""
app/core/transfer_reconciliation.py
====================================
Blind check for internal location-to-location / department-to-department
stock transfers. Mirrors the shape of checkout_reconciliation.py (which
covers customer-facing dispatch) but for internal movement, e.g.
Manufacturing -> Commercial, or Cold Store -> Dry Ice Storage.

BLIND CHECK PRINCIPLE:
The issuing location records what it sent (quantity + transfer reference
number). The receiving department must NOT see either value before
submitting their own count -- they independently enter what they counted
and what reference number is on the physical transfer note. Matching is
decided server-side by comparing the two independently-entered records,
not by the receiver confirming a number they were shown. This is what
makes it a *blind* check rather than a rubber-stamp.

Status lifecycle:
    Pending -> Received-Matched      (quantity AND ref number both match)
            -> Received-Mismatch     (either field disagrees; needs review)

Dual-backend: Supabase table `location_transfers` when available, with an
in-memory session_state fallback list, following the same pattern as
checkout_reconciliation.py elsewhere in this app.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
import streamlit as st

from core.error_handling import logger

_SESSION_KEY = "_location_transfers_fallback"
_SUPABASE_TABLE = "location_transfers"


def _fallback_store() -> list:
    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = []
    return st.session_state[_SESSION_KEY]


def init_transfer_storage(supabase_client: Any = None) -> None:
    """Ensures the session_state fallback list exists. If a Supabase
    client is supplied, assumes the `location_transfers` table has
    already been created via migration (same convention as
    `cheese_returns` / `app_users` elsewhere in this app) -- this
    function does not attempt DDL, just verifies reachability and logs
    a warning so a missing table fails loud instead of silently
    falling through to the session-only store on every page load."""
    _fallback_store()  # ensures list exists regardless of backend

    if supabase_client is not None:
        try:
            supabase_client.table(_SUPABASE_TABLE).select("id").limit(1).execute()
        except Exception as e:
            logger.warning(
                f"location_transfers table not reachable in Supabase "
                f"({e}) -- transfers will use the session-only fallback "
                f"until the table exists."
            )


def _next_fallback_id() -> int:
    store = _fallback_store()
    return (max((r["id"] for r in store), default=0)) + 1


def _generate_transfer_ref(supabase_client: Any = None) -> str:
    """Auto-generates a sequential reference (TR-0001, TR-0002, ...) instead
    of relying on manual entry -- removes typo risk from one of the two
    fields the blind check compares. Shown back to the issuer once, right
    after sending, so it can be written on the physical transfer note --
    the receiver still only ever sees it via that note, never via the app,
    so the blind check itself is unaffected."""
    existing = get_transfers(supabase_client=supabase_client)
    return f"TR-{len(existing) + 1:04d}"


def record_transfer(
    transfer_date,
    item_name: str,
    quantity_issued: float,
    from_location: str,
    to_location: str,
    issued_by: str,
    unit: str = "kg",
    issue_notes: str = "",
    supabase_client: Any = None,
):
    """Issuing side. Records what was sent. Returns (new_id,
    transfer_ref_number) so the caller can show the generated reference
    back to the issuer for writing on the physical note."""
    transfer_ref_number = _generate_transfer_ref(supabase_client=supabase_client)
    record = {
        "transfer_date": str(transfer_date),
        "item_name": item_name,
        "quantity_issued": float(quantity_issued),
        "unit": unit,
        "from_location": from_location,
        "to_location": to_location,
        "transfer_ref_number": transfer_ref_number,
        "issued_by": issued_by,
        "issue_notes": issue_notes,
        "status": "Pending",
        "quantity_received": None,
        "received_ref_number": None,
        "received_by": None,
        "received_at": None,
        "reconciliation_notes": None,
        "created_at": datetime.now().isoformat(),
    }

    if supabase_client is not None:
        try:
            result = supabase_client.table(_SUPABASE_TABLE).insert(record).execute()
            return result.data[0]["id"], transfer_ref_number
        except Exception as e:
            logger.error(f"Supabase insert failed for location_transfers, falling back: {e}")

    store = _fallback_store()
    record["id"] = _next_fallback_id()
    store.append(record)
    return record["id"], transfer_ref_number


def get_transfers(supabase_client: Any = None) -> list:
    if supabase_client is not None:
        try:
            result = supabase_client.table(_SUPABASE_TABLE).select("*").order("id").execute()
            if result.data:
                return result.data
        except Exception as e:
            logger.error(f"Supabase fetch failed for location_transfers, falling back: {e}")

    return list(_fallback_store())


def _get_transfer_by_id(transfer_id: int, supabase_client: Any = None) -> Optional[dict]:
    for t in get_transfers(supabase_client=supabase_client):
        if t["id"] == transfer_id:
            return t
    return None


def receive_transfer_blind(
    transfer_id: int,
    received_by: str,
    quantity_received: float,
    received_ref_number: str,
    condition_notes: str = "",
    quantity_tolerance: float = 0.01,
    supabase_client: Any = None,
) -> dict:
    """Receiving side -- the blind entry point. Caller must NOT have shown
    the receiver quantity_issued or transfer_ref_number before calling this;
    those are only read here, server-side, for comparison.

    Returns {"matched": bool, "notes": str} so the UI can react without
    needing to re-derive the comparison itself.
    """
    original = _get_transfer_by_id(transfer_id, supabase_client=supabase_client)
    if original is None:
        return {"matched": False, "notes": "Transfer record not found."}

    qty_match = abs(float(quantity_received) - float(original["quantity_issued"])) <= quantity_tolerance
    ref_match = (
        received_ref_number.strip().lower()
        == str(original["transfer_ref_number"]).strip().lower()
    )
    matched = qty_match and ref_match

    if matched:
        notes = "Blind check matched — quantity and transfer reference both correct."
    else:
        parts = []
        if not qty_match:
            parts.append(
                f"quantity: issued {original['quantity_issued']} {original['unit']} "
                f"vs received {quantity_received} {original['unit']}"
            )
        if not ref_match:
            parts.append(
                f"ref #: issued note read '{original['transfer_ref_number']}' "
                f"vs receiver read '{received_ref_number.strip()}'"
            )
        notes = "Blind check MISMATCH — " + "; ".join(parts)
        if condition_notes.strip():
            notes += f" | Receiver notes: {condition_notes.strip()}"

    status = "Received-Matched" if matched else "Received-Mismatch"
    updates = {
        "status": status,
        "quantity_received": float(quantity_received),
        "received_ref_number": received_ref_number.strip(),
        "received_by": received_by,
        "received_at": datetime.now().isoformat(),
        "reconciliation_notes": notes,
    }

    if supabase_client is not None:
        try:
            supabase_client.table(_SUPABASE_TABLE).update(updates).eq("id", transfer_id).execute()
            return {"matched": matched, "notes": notes}
        except Exception as e:
            logger.error(f"Supabase update failed for location_transfers, falling back: {e}")

    store = _fallback_store()
    for r in store:
        if r["id"] == transfer_id:
            r.update(updates)
            break

    return {"matched": matched, "notes": notes}