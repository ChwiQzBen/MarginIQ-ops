"""
app/core/locations.py
======================
Single source of truth for the company's physical locations, shared
between Transfers (app/core/all_items_ui.py) and Stock Take
(app/core/stock_take.py) so a location added in one place doesn't
silently drift out of sync with the other -- which is exactly what had
already happened before this file existed (Stock Take's location
dropdown had four made-up names that matched nothing real).
"""

COMPANY_LOCATIONS = [
    "Tigoni Warehouse",
    "Main Factory Stores",
    "Inside Factory Stores",
]