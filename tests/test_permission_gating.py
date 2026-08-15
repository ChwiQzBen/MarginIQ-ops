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


def test_commercial_mode_shows_warning_with_no_permissions(tmp_path):
    script = _write_harness(tmp_path, "app.core.commercial_ui", "render_commercial_mode", None)
    at = AppTest.from_file(str(script))
    at.run()
    assert not at.exception, f"render_commercial_mode raised: {at.exception}"
    assert any("isn't available for your current role" in w.value for w in at.warning)
    # No radio should render at all -- nothing to pick between
    assert len(at.radio) == 0


def test_commercial_mode_shows_only_permitted_tab(tmp_path):
    script = _write_harness(tmp_path, "app.core.commercial_ui", "render_commercial_mode", "manage_lpo")
    at = AppTest.from_file(str(script))
    at.run()
    assert not at.exception, f"render_commercial_mode raised: {at.exception}"
    assert len(at.radio) == 1, "exactly one tab selector should render"
    assert at.radio[0].options == ["📄 LPO Register"], (
        f"only the permitted tab should be visible, got {at.radio[0].options}"
    )


def test_all_items_mode_shows_warning_with_no_permissions(tmp_path):
    script = _write_harness(tmp_path, "app.core.all_items_ui", "render_all_items_mode", None)
    at = AppTest.from_file(str(script))
    at.run()
    assert not at.exception, f"render_all_items_mode raised: {at.exception}"
    assert any("isn't available for your current role" in w.value for w in at.warning)


def test_cheese_production_mode_shows_warning_with_no_permissions(tmp_path):
    script = _write_harness(tmp_path, "app.core.cheese_production_ui", "render_cheese_production_mode", None)
    at = AppTest.from_file(str(script))
    at.run()
    assert not at.exception, f"render_cheese_production_mode raised: {at.exception}"
    assert any("isn't available for your current role" in w.value for w in at.warning)