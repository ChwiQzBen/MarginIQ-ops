"""
app/core/inventory_cache.py
=============================
Cross-restart snapshot cache for All Items Mode's three Google-Sheets-backed
loaders (Inventory, Stock Movements, Analytics). Streamlit Cloud's local
filesystem AND st.session_state both reset on every container restart /
redeploy -- the session-level "last known good" fallback already in
all_items_ui.py's loaders only covers a hiccup WITHIN one running session.
This module is the layer underneath that: the last successful load,
persisted to Supabase, survives a restart too.

Supabase-only by design -- there's no local-disk fallback here, because a
local file would be just as ephemeral as session_state on Streamlit Cloud
and would add complexity for zero real benefit. If Supabase is unavailable,
callers simply don't get a cross-restart snapshot and fall through to
their own error state, same as before this module existed.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
import io
import json
import pandas as pd

CACHE_TABLE = "inventory_data_cache"


def _frames_to_payload(frames: Dict[str, pd.DataFrame], scalars: Optional[Dict[str, Any]] = None) -> str:
    """Pure serialization step, factored out so it can be unit-tested
    without a real Supabase client -- see the self-test at the bottom."""
    encoded = {
        name: df.to_json(orient="split", date_format="iso")
        for name, df in frames.items()
    }
    return json.dumps({"frames": encoded, "scalars": scalars or {}})


def _payload_to_frames(payload: str) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """Inverse of _frames_to_payload."""
    parsed = json.loads(payload)
    frames = {
        name: pd.read_json(io.StringIO(raw), orient="split")
        for name, raw in parsed.get("frames", {}).items()
    }
    return frames, parsed.get("scalars", {})


def save_snapshot(cache_key: str, frames: Dict[str, pd.DataFrame],
                   scalars: Optional[Dict[str, Any]] = None, supabase_client=None) -> None:
    """Best-effort -- a failed save here should never interrupt the page
    render that triggered it, so failures are swallowed, not raised."""
    if not supabase_client:
        return
    try:
        payload = _frames_to_payload(frames, scalars)
        supabase_client.table(CACHE_TABLE).upsert({
            "cache_key": cache_key, "payload": payload,
            "cached_at": datetime.now().isoformat(),
        }).execute()
    except Exception:
        pass  # snapshotting is a nice-to-have, never worth surfacing to the user


def load_snapshot(cache_key: str, supabase_client=None) -> Optional[Tuple[Dict[str, pd.DataFrame], Dict[str, Any], datetime]]:
    """Returns (frames, scalars, cached_at) or None if nothing's saved yet
    (first-ever run) or Supabase is unavailable."""
    if not supabase_client:
        return None
    try:
        result = supabase_client.table(CACHE_TABLE).select("*").eq("cache_key", cache_key).execute()
        if not result.data:
            return None
        row = result.data[0]
        frames, scalars = _payload_to_frames(row["payload"])
        cached_at = datetime.fromisoformat(row["cached_at"])
        return frames, scalars, cached_at
    except Exception:
        return None


if __name__ == "__main__":
    print("=" * 60)
    print("Round-trip serialization (no Supabase needed)")
    print("=" * 60)
    stock = pd.DataFrame({
        "ITEM_NAME": ["Widget A", "Widget B"],
        "QUANTITY": [10, 25],
        "UNIT PRICE": [100.5, 50.0],
    })
    empty = pd.DataFrame(columns=["ITEM_NAME", "QUANTITY"])

    payload = _frames_to_payload(
        {"stock": stock, "empty": empty}, scalars={"category_count": 3}
    )
    frames_back, scalars_back = _payload_to_frames(payload)

    print(frames_back["stock"])
    assert list(frames_back["stock"]["ITEM_NAME"]) == ["Widget A", "Widget B"]
    assert frames_back["stock"]["QUANTITY"].sum() == 35
    assert frames_back["empty"].empty
    assert scalars_back["category_count"] == 3

    print("\nTest: save_snapshot/load_snapshot no-op safely with no client")
    save_snapshot("test_key", {"stock": stock}, supabase_client=None)  # should not raise
    result = load_snapshot("test_key", supabase_client=None)
    assert result is None

    print("\nAll inventory_cache checks passed.")