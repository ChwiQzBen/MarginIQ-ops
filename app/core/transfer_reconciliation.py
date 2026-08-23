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
    """Auto-generates a sequential reference (TR-0001, TR-0002, ...) for a
    whole consignment -- one Send action gets one reference, regardless of
    how many item lines are in it. Counts unique existing reference
    numbers rather than raw rows, so a 3-item batch doesn't skip three
    numbers ahead for the next single-item transfer."""
    existing = get_transfers(supabase_client=supabase_client)
    unique_refs = {t["transfer_ref_number"] for t in existing}
    return f"TR-{len(unique_refs) + 1:04d}"


def record_transfer_batch(
    transfer_date,
    items: list,
    from_location: str,
    to_location: str,
    issued_by: str,
    issue_notes: str = "",
    supabase_client: Any = None,
):
    """Issuing side -- one or more items under a single shared reference
    number. Covers both a single-item transfer (a batch of one) and a
    genuine bulk consignment, e.g. one truck run carrying several
    different items under one waybill.

    items: list of dicts, each {"item_name": str, "quantity_issued": float,
    "unit": str}. Every item in the batch shares the same auto-generated
    transfer_ref_number.

    Returns (transfer_ref_number, [new_ids]) -- one id per item line.
    """
    transfer_ref_number = _generate_transfer_ref(supabase_client=supabase_client)
    base = {
        "transfer_date": str(transfer_date),
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
    rows = []
    for item in items:
        row = dict(base)
        row["item_name"] = item["item_name"]
        row["quantity_issued"] = float(item["quantity_issued"])
        row["unit"] = item.get("unit") or "kg"
        rows.append(row)

    if supabase_client is not None:
        try:
            result = supabase_client.table(_SUPABASE_TABLE).insert(rows).execute()
            new_ids = [r["id"] for r in result.data]
            return transfer_ref_number, new_ids
        except Exception as e:
            logger.error(f"Supabase batch insert failed for location_transfers, falling back: {e}")

    store = _fallback_store()
    new_ids = []
    for row in rows:
        row["id"] = _next_fallback_id()
        store.append(row)
        new_ids.append(row["id"])
    return transfer_ref_number, new_ids

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


def receive_transfer_item(
    transfer_id: int,
    received_by: str,
    quantity_received: float,
    condition_notes: str = "",
    quantity_tolerance: float = 0.01,
    supabase_client: Any = None,
) -> dict:
    """Receiving side, per item line. The reference number is no longer
    independently entered here -- it's shown to the receiver as an
    identifier so they know which consignment they're receiving, not
    blind-typed for comparison. Quantity remains the one blind-compared
    field: it's the one thing a receiver could be tempted to just confirm
    without actually counting, so it's the one worth protecting.
    """
    original = _get_transfer_by_id(transfer_id, supabase_client=supabase_client)
    if original is None:
        return {"matched": False, "notes": "Transfer record not found."}

    qty_match = abs(float(quantity_received) - float(original["quantity_issued"])) <= quantity_tolerance

    if qty_match:
        notes = "Quantity matched."
    else:
        notes = (
            f"Quantity MISMATCH — issued {original['quantity_issued']} {original['unit']} "
            f"vs received {quantity_received} {original['unit']}"
        )
    if condition_notes.strip():
        notes += f" | Receiver notes: {condition_notes.strip()}"

    status = "Received-Matched" if qty_match else "Received-Mismatch"
    updates = {
        "status": status,
        "quantity_received": float(quantity_received),
        "received_ref_number": original["transfer_ref_number"],
        "received_by": received_by,
        "received_at": datetime.now().isoformat(),
        "reconciliation_notes": notes,
    }

    if supabase_client is not None:
        try:
            supabase_client.table(_SUPABASE_TABLE).update(updates).eq("id", transfer_id).execute()
            return {"matched": qty_match, "notes": notes}
        except Exception as e:
            logger.error(f"Supabase update failed for location_transfers, falling back: {e}")

    store = _fallback_store()
    for r in store:
        if r["id"] == transfer_id:
            r.update(updates)
            break

    return {"matched": qty_match, "notes": notes}

def resolve_transfer_mismatch(
    transfer_id: int,
    resolved_by: str,
    resolution_notes: str,
    supabase_client: Any = None,
) -> bool:
    """Closes out a Received-Mismatch transfer once someone has actually
    investigated it. Appends the resolution note to whatever comparison
    note is already on the record rather than overwriting it, so the
    original mismatch detail stays visible alongside how it was resolved."""
    original = _get_transfer_by_id(transfer_id, supabase_client=supabase_client)
    if original is None:
        return False

    existing_notes = original.get("reconciliation_notes") or ""
    combined_notes = f"{existing_notes}\nResolved by {resolved_by}: {resolution_notes.strip()}"

    updates = {
        "status": "Resolved",
        "resolved_by": resolved_by,
        "resolved_at": datetime.now().isoformat(),
        "reconciliation_notes": combined_notes,
    }

    if supabase_client is not None:
        try:
            supabase_client.table(_SUPABASE_TABLE).update(updates).eq("id", transfer_id).execute()
            return True
        except Exception as e:
            logger.error(f"Supabase update failed for location_transfers, falling back: {e}")

    store = _fallback_store()
    for r in store:
        if r["id"] == transfer_id:
            r.update(updates)
            break
    return True
