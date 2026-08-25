"""
tests/test_permission_gating.py
==================================
AppTest coverage for the permission-gated tab visibility pattern shared by
render_commercial_mode, render_all_items_mode, and render_cheese_production_mode:
each filters its tab list through has_permission(), and shows a fixed warning
if the result is empty. A regression here is a security-relevant bug (a role
seeing tabs it shouldn't, or being wrongly locked out), not a cosmetic one --
that's why this pattern gets AppTest coverage before anything purely visual.

Run from the repo root: pytest tests/test_permission_gating.py -v

NOTE: These run against your REAL render functions with supabase_client=None,
relying on the existing dual-backend (Supabase primary / SQLite fallback)
architecture -- no mocking needed. If a function tries a network call before
falling back, that's itself worth knowing about.
"""
import sys
import os
from streamlit.testing.v1 import AppTest

# Adjust if your test file lives somewhere other than tests/ at repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_harness(tmp_path, module_path: str, entry_fn: str, allowed_permission: str | None):
    """Writes a tiny standalone script that AppTest.from_file() can run --
    calls the real entry point with a has_permission callable that allows
    exactly one permission (or none), and supabase_client=None so the
    dual-backend fallback does the rest."""
    script = tmp_path / "harness.py"
    script.write_text(f"""
from {module_path} import {entry_fn}

def has_permission(perm):
    return perm == {allowed_permission!r}

{entry_fn}(supabase_client=None, has_permission=has_permission)
""")
    return script


def _run_app(script) -> "AppTest":
    """AppTest's default timeout (3s) is tuned for a lightweight script --
    too tight for the first import of this app's heavy dependency graph
    (gspread, google-auth, production_tracking, newsvendor_engine, etc.)
    in a fresh process. 30s absorbs a genuinely cold import without
    masking an actual hang, which would still exceed 30s."""
    at = AppTest.from_file(str(script), default_timeout=30)
    at.run(timeout=30)
    return at


def _write_all_items_harness(tmp_path, allowed_permission: str | None):
    """render_all_items_mode's signature genuinely differs from the other
    two entry points -- it takes an AllItemsContext object as its first
    argument, not a bare supabase_client kwarg (all_items_ui.py's tabs
    pull their data through one context object). Every AllItemsContext
    field defaults to None, which is fine for this test: the permission
    check runs and returns before any field on ctx is ever touched."""
    script = tmp_path / "harness.py"
    script.write_text(f"""
from app.core.all_items_ui import render_all_items_mode, AllItemsContext

def has_permission(perm):
    return perm == {allowed_permission!r}

ctx = AllItemsContext()
render_all_items_mode(ctx, has_permission=has_permission)
""")
    return script


def _write_all_items_harness_multi(tmp_path, allowed_permissions: list):
    """Same as _write_all_items_harness, but allows a SET of permissions
    rather than exactly one -- needed for Checkout Reconciliation, which
    is gated twice: RECONCILE_CHECKOUTS decides whether the tab shows up
    at all; REVIEW_RECONCILIATION, checked separately inside the tab
    function itself, decides whether the review/oversight section below
    the recording form renders. A real role always grants permissions
    together (e.g. 'manager' has both) -- the single-permission harness
    above can't reach that second gate, since a user who only passes
    REVIEW_RECONCILIATION would never see the tab in the first place to
    get inside it."""
    script = tmp_path / "harness.py"
    perms_repr = repr(set(allowed_permissions))
    script.write_text(f"""
from app.core.all_items_ui import render_all_items_mode, AllItemsContext

_ALLOWED = {perms_repr}

def has_permission(perm):
    return perm in _ALLOWED

ctx = AllItemsContext()
render_all_items_mode(ctx, has_permission=has_permission)
""")
    return script


def test_commercial_mode_shows_warning_with_no_permissions(tmp_path):
    script = _write_harness(tmp_path, "app.core.commercial_ui", "render_commercial_mode", None)
    at = _run_app(script)
    assert not at.exception, f"render_commercial_mode raised: {at.exception}"
    assert any("isn't available for your current role" in w.value for w in at.warning)
    # No radio should render at all -- nothing to pick between
    assert len(at.radio) == 0


def test_commercial_mode_shows_only_permitted_tab(tmp_path):
    script = _write_harness(tmp_path, "app.core.commercial_ui", "render_commercial_mode", "manage_lpo")
    at = _run_app(script)
    assert not at.exception, f"render_commercial_mode raised: {at.exception}"
    assert len(at.radio) == 1, "exactly one tab selector should render"
    assert at.radio[0].options == ["📄 LPO Register"], (
        f"only the permitted tab should be visible, got {at.radio[0].options}"
    )


def test_all_items_mode_shows_warning_with_no_permissions(tmp_path):
    script = _write_all_items_harness(tmp_path, None)
    at = _run_app(script)
    assert not at.exception, f"render_all_items_mode raised: {at.exception}"
    assert any("isn't available for your current role" in w.value for w in at.warning)


def test_cheese_production_mode_shows_warning_with_no_permissions(tmp_path):
    script = _write_harness(tmp_path, "app.core.cheese_production_ui", "render_cheese_production_mode", None)
    at = _run_app(script)
    assert not at.exception, f"render_cheese_production_mode raised: {at.exception}"
    assert any("isn't available for your current role" in w.value for w in at.warning)


# ============================================================
# New coverage: Transfers, Stock Variance, and Checkout
# Reconciliation's two-tier gate -- all added the same night.
# ============================================================

def test_all_items_mode_transfers_tab_gated_correctly(tmp_path):
    """Also a regression check against the 'blind check' language leak
    fixed earlier tonight: the tab was rewritten to give the receiver
    zero awareness that a comparison is happening at all, so nothing
    rendered here should mention 'blind' in any form."""
    script = _write_all_items_harness(tmp_path, "record_transfers")
    at = _run_app(script)
    assert not at.exception, f"render_all_items_mode raised: {at.exception}"
    assert len(at.radio) == 1, "exactly one tab selector should render"
    assert at.radio[0].options == ["🔁 Transfers"], (
        f"only the permitted tab should be visible, got {at.radio[0].options}"
    )
    all_text = " ".join(
        el.value for group in (at.markdown, at.caption) for el in group if isinstance(el.value, str)
    )
    assert "blind" not in all_text.lower(), (
        "Transfers tab should never mention 'blind' -- the receiver must not know "
        "a comparison is happening at all, not just be blind to the expected answer"
    )


def test_all_items_mode_stock_variance_tab_gated_correctly(tmp_path):
    script = _write_all_items_harness(tmp_path, "review_reconciliation")
    at = _run_app(script)
    assert not at.exception, f"render_all_items_mode raised: {at.exception}"
    assert len(at.radio) == 1, "exactly one tab selector should render"
    assert at.radio[0].options == ["📊 Stock Variance"], (
        f"only the permitted tab should be visible, got {at.radio[0].options}"
    )


def test_stock_variance_reports_insufficient_data_with_no_stock_takes(tmp_path):
    """With a fresh session (no completed Stock Takes at all), the report
    should explain what's missing rather than error or silently show
    nothing -- this exercises the len(completed) < 2 early-return path in
    _compute_stock_variance without ever reaching the GoogleSheetReader
    call further down, since that path returns before touching it."""
    script = _write_all_items_harness(tmp_path, "review_reconciliation")
    at = _run_app(script)
    assert not at.exception, f"render_all_items_mode raised: {at.exception}"
    all_info = " ".join(el.value for el in at.info if isinstance(el.value, str))
    assert "completed Stock Take" in all_info, (
        f"expected an explanation of the missing-data precondition, got info blocks: {all_info!r}"
    )


def test_checkout_reconciliation_hides_review_section_without_review_permission(tmp_path):
    """Two-tier gating: RECONCILE_CHECKOUTS alone should show the
    recording form but NOT the Mismatched/Matched/Resolved review
    section -- that second gate is REVIEW_RECONCILIATION, checked inside
    the tab function itself, independent of tab-level visibility."""
    script = _write_all_items_harness_multi(tmp_path, ["reconcile_checkouts"])
    at = _run_app(script)
    assert not at.exception, f"render_all_items_mode raised: {at.exception}"
    all_markdown = " ".join(el.value for el in at.markdown if isinstance(el.value, str))
    assert "Record a Check-Out" in all_markdown, "the recording form should always render"
    assert "Transfers Awaiting Reconciliation" not in all_markdown, (
        "the review section should NOT render without REVIEW_RECONCILIATION"
    )


def test_checkout_reconciliation_shows_review_section_with_review_permission(tmp_path):
    script = _write_all_items_harness_multi(tmp_path, ["reconcile_checkouts", "review_reconciliation"])
    at = _run_app(script)
    assert not at.exception, f"render_all_items_mode raised: {at.exception}"
    all_markdown = " ".join(el.value for el in at.markdown if isinstance(el.value, str))
    assert "Transfers Awaiting Reconciliation" in all_markdown, (
        "the review section should render with both permissions granted, as a real role would have"
    )