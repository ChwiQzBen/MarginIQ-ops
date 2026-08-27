"""
app/core/transfer_reconciliation.py
====================================
Two separate, linked documents, mirroring how this actually works on
paper:

  Transfer (TR-XXXX)  -- the dispatch record. Source store, destination
  store, transfer date, requested/approved/dispatched by, items +
  quantities sent. Lives in location_transfers.

  Goods Received Note (GRN-XXXX) -- the receiving record. A SEPARATE
  document that references the Transfer it's receiving against (never
  reuses or generates a new Transfer number) -- receiving store, receiving
  date, received by, items + quantities received, variance against what
  the Transfer said was sent, receiving status. Lives in
  goods_received_notes, one row per item line, sharing one grn_number per
  receiving action the same way multiple items share one
  transfer_ref_number per Send action.

  Receipt status ("Pending" / "Partially Received" / "Received") for a
  Transfer is never stored -- it's computed live from whether a GRN row
  exists referencing each of that Transfer's item lines (see
  get_pending_transfer_lines / get_transfer_receipt_status), so it can't
  drift out of sync with the GRNs that are the actual source of truth
  for it.

Blind check property preserved from the original single-table design:
quantity_received on the GRN is the one blind-compared field. The
receiver identifies which Transfer/items they're receiving by reading
the reference off the physical note (shown openly, not blind-typed),
same as before.
"""
from datetime import datetime
from typing import Optional, Any
import streamlit as st

from core.error_handling import logger

_SESSION_KEY = "_location_transfers_fallback"
_GRN_SESSION_KEY = "_goods_received_notes_fallback"
_SUPABASE_TABLE = "location_transfers"
_GRN_TABLE = "goods_received_notes"


def _fallback_store() -> list:
    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = []
    return st.session_state[_SESSION_KEY]


def _grn_fallback_store() -> list:
    if _GRN_SESSION_KEY not in st.session_state:
        st.session_state[_GRN_SESSION_KEY] = []
    return st.session_state[_GRN_SESSION_KEY]


def init_transfer_storage(supabase_client: Any = None) -> None:
    _fallback_store()
    _grn_fallback_store()
    if supabase_client is not None:
        try:
            supabase_client.table(_SUPABASE_TABLE).select("id").limit(1).execute()
            supabase_client.table(_GRN_TABLE).select("id").limit(1).execute()
        except Exception as e:
            logger.warning(
                f"location_transfers / goods_received_notes not reachable in "
                f"Supabase ({e}) -- falling back to session-only storage until "
                f"the tables exist and are reachable."
            )


def _next_fallback_id(store: list) -> int:
    return (max((r["id"] for r in store), default=0)) + 1


def _generate_transfer_ref(supabase_client: Any = None) -> str:
    """Auto-generates a sequential reference (TR-0001, TR-0002, ...).
    When Supabase is reachable, uses a Postgres sequence via RPC
    (next_transfer_ref) for atomic, race-condition-free numbering.
    Falls back to counting existing rows only when Supabase is
    unavailable -- in that mode there's no database to be atomic
    against anyway."""
    if supabase_client is not None:
        try:
            result = supabase_client.rpc('next_transfer_ref').execute()
            if result.data:
                return result.data
        except Exception as e:
            logger.error(f"next_transfer_ref RPC failed, falling back to count-based numbering: {e}")

    existing = get_transfers(supabase_client=supabase_client)
    unique_refs = {t["transfer_ref_number"] for t in existing}
    return f"TR-{len(unique_refs) + 1:04d}"


def _generate_grn_number(supabase_client: Any = None) -> str:
    """Same atomic-sequence approach as _generate_transfer_ref, on its
    own independent sequence -- GRN-0001, GRN-0002, ... has nothing to
    do with how many Transfers exist, only how many receiving actions
    have happened."""
    if supabase_client is not None:
        try:
            result = supabase_client.rpc('next_grn_number').execute()
            if result.data:
                return result.data
        except Exception as e:
            logger.error(f"next_grn_number RPC failed, falling back to count-based numbering: {e}")

    existing = get_goods_received_notes(supabase_client=supabase_client)
    unique_grns = {g["grn_number"] for g in existing}
    return f"GRN-{len(unique_grns) + 1:04d}"


def record_transfer_batch(
    transfer_date,
    items: list,
    from_location: str,
    to_location: str,
    requested_by: str = "",
    approved_by: str = "",
    dispatched_by: str = "",
    issue_notes: str = "",
    supabase_client: Any = None,
):
    """Issuing side -- one or more items under a single shared reference
    number. Dispatch-only fields -- this document never carries
    receiving data, that's the GRN's job now.

    Returns (transfer_ref_number, [new_ids]) -- one id per item line.
    """
    transfer_ref_number = _generate_transfer_ref(supabase_client=supabase_client)
    base = {
        "transfer_date": str(transfer_date),
        "from_location": from_location,
        "to_location": to_location,
        "transfer_ref_number": transfer_ref_number,
        "requested_by": requested_by,
        "approved_by": approved_by,
        "issued_by": dispatched_by,  # original column name kept; represents "dispatched by" going forward
        "issue_notes": issue_notes,
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
        row["id"] = _next_fallback_id(store)
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


def get_goods_received_notes(supabase_client: Any = None) -> list:
    if supabase_client is not None:
        try:
            result = supabase_client.table(_GRN_TABLE).select("*").order("id").execute()
            if result.data:
                return result.data
        except Exception as e:
            logger.error(f"Supabase fetch failed for goods_received_notes, falling back: {e}")
    return list(_grn_fallback_store())


def _get_transfer_by_id(transfer_id: int, supabase_client: Any = None) -> Optional[dict]:
    for t in get_transfers(supabase_client=supabase_client):
        if t["id"] == transfer_id:
            return t
    return None


def get_pending_transfer_lines(supabase_client: Any = None) -> list:
    """Transfer lines with no GRN referencing them yet -- what's still
    awaiting receipt. Computed live from the join rather than stored,
    so it can't drift out of sync with the GRNs that are the real
    source of truth for receipt state."""
    all_transfers = get_transfers(supabase_client=supabase_client)
    all_grns = get_goods_received_notes(supabase_client=supabase_client)
    received_ids = {g["transfer_item_id"] for g in all_grns}
    return [t for t in all_transfers if t["id"] not in received_ids]


def get_transfer_receipt_status(transfer_ref_number: str, supabase_client: Any = None) -> str:
    """'Pending' / 'Partially Received' / 'Received' for a whole
    Transfer (all lines sharing one transfer_ref_number)."""
    all_transfers = get_transfers(supabase_client=supabase_client)
    all_grns = get_goods_received_notes(supabase_client=supabase_client)
    received_ids = {g["transfer_item_id"] for g in all_grns}
    lines = [t for t in all_transfers if t["transfer_ref_number"] == transfer_ref_number]
    if not lines:
        return "Unknown"
    received_count = sum(1 for t in lines if t["id"] in received_ids)
    if received_count == 0:
        return "Pending"
    if received_count == len(lines):
        return "Received"
    return "Partially Received"


def create_grn(
    transfer_ref_number: str,
    receiving_store: str,
    receiving_date,
    received_by: str,
    item_receipts: list,
    supabase_client: Any = None,
):
    """Receiving side -- a brand new document (its own GRN-XXXX number),
    never a new Transfer number. item_receipts: list of dicts, each
    {"transfer_item_id": int, "item_name": str, "quantity_expected":
    float, "quantity_received": float, "unit": str}.

    quantity_received is the one blind-compared field here -- the
    receiver identifies which Transfer/items to receive against by
    reading the reference off the physical note (shown openly on
    screen, not blind-typed); only the count itself is never shown to
    them ahead of time.

    Returns (grn_number, [new_ids]).
    """
    grn_number = _generate_grn_number(supabase_client=supabase_client)
    rows = []
    for item in item_receipts:
        qty_expected = float(item["quantity_expected"])
        qty_received = float(item["quantity_received"])
        matched = abs(qty_received - qty_expected) <= 0.01
        unit = item.get("unit") or "kg"
        notes = (
            "Quantity matched." if matched else
            f"Quantity MISMATCH — expected {qty_expected} {unit} vs received {qty_received} {unit}"
        )
        rows.append({
            "grn_number": grn_number,
            "transfer_ref_number": transfer_ref_number,
            "transfer_item_id": item["transfer_item_id"],
            "item_name": item["item_name"],
            "unit": unit,
            "quantity_expected": qty_expected,
            "quantity_received": qty_received,
            "receiving_store": receiving_store,
            "receiving_date": str(receiving_date),
            "received_by": received_by,
            "receiving_status": "Matched" if matched else "Mismatch",
            "reconciliation_notes": notes,
            "created_at": datetime.now().isoformat(),
        })

    if supabase_client is not None:
        try:
            result = supabase_client.table(_GRN_TABLE).insert(rows).execute()
            new_ids = [r["id"] for r in result.data]
            return grn_number, new_ids
        except Exception as e:
            logger.error(f"Supabase batch insert failed for goods_received_notes, falling back: {e}")

    store = _grn_fallback_store()
    new_ids = []
    for row in rows:
        row["id"] = _next_fallback_id(store)
        store.append(row)
        new_ids.append(row["id"])
    return grn_number, new_ids


def resolve_grn_mismatch(
    grn_id: int,
    resolved_by: str,
    resolution_notes: str,
    supabase_client: Any = None,
) -> bool:
    """Closes out a Mismatch GRN line once someone has actually
    investigated it. Appends the resolution note to whatever
    comparison note is already there rather than overwriting it."""
    all_grns = get_goods_received_notes(supabase_client=supabase_client)
    original = next((g for g in all_grns if g["id"] == grn_id), None)
    if original is None:
        return False

    existing_notes = original.get("reconciliation_notes") or ""
    combined_notes = f"{existing_notes}\nResolved by {resolved_by}: {resolution_notes.strip()}"
    updates = {
        "receiving_status": "Resolved",
        "resolved_by": resolved_by,
        "resolved_at": datetime.now().isoformat(),
        "reconciliation_notes": combined_notes,
    }

    if supabase_client is not None:
        try:
            supabase_client.table(_GRN_TABLE).update(updates).eq("id", grn_id).execute()
            return True
        except Exception as e:
            logger.error(f"Supabase update failed for goods_received_notes, falling back: {e}")

    store = _grn_fallback_store()
    for r in store:
        if r["id"] == grn_id:
            r.update(updates)
            break
    return True