"""
scripts/add_missing_items.py
==============================
One-time bulk-add for items found unmatched by
check_migration_readiness.py -- these show up in Check-In/Check-Out
history but were never added to the Stock/Inventory sheet's item list,
which also means they've been missing from item_master and unselectable
in the Check-In/Check-Out forms.

Category and unit below are a best guess from the item name alone --
review and correct via the Inventory tab's Add/Edit Item form after
running this. Price and reorder level are deliberately left at 0; this
only unblocks the import and the item pickers, it doesn't guess pricing.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _migration_db import init_supabase_admin
from app.core.item_master import create_item, get_all_items

MISSING_ITEMS = [
    # (item_name, category, unit_of_measure)
    ("200ml Jam Jars", "Packaging", "pcs"),
    ("Coffee powder / kg", "Ingredients", "kg"),
    ("Dome tubs", "Packaging", "pcs"),
    ("Formaldehyde or Formalin /2.5 kg", "Chemicals", "kg"),
    ("Hog Casing", "Packaging", "pcs"),
    ("Mortein Doom", "Chemicals", "pcs"),
    ("Tumeric powder /100g", "Ingredients", "kg"),
    ("WHITE BUTTER BUCKETS 20L", "Packaging", "pcs"),
    ("brush", "Equipment", "pcs"),
    ("1000ml Clear Plastic Tubs", "Packaging", "pcs"),
    ("100mL Mango Sorbet TUBS", "Packaging", "pcs"),
    ("201 ml NEW - White TUBS", "Packaging", "pcs"),
    ("4Ltr Container", "Packaging", "pcs"),
    ("6KG Gas Stand", "Equipment", "pcs"),
    ("Acetic acid", "Chemicals", "L"),
    ("CheeseLove Block bags (30*100)", "Packaging", "pcs"),
    ("Chlorine Liquid / Ltr", "Chemicals", "L"),
    ("ELECTRIC KETTLE", "Equipment", "pcs"),
    ("Farm green overalls", "Safety", "pcs"),
    ("G/boots Black Heavy duty 009", "Safety", "pcs"),
    ("G/boots white Heavy duty  010", "Safety", "pcs"),
    ("Hard Broom wooden handle", "Equipment", "pcs"),
    ("Ice cream Cups wooden", "Packaging", "pcs"),
    ("Iodated Fine Salt / 50 Kg bag", "Ingredients", "kg"),
    ("Lemon Pepper Feta bags/Bocconcini  (40*50)", "Packaging", "pcs"),
    ("Mafuco Bags -Small", "Packaging", "pcs"),
    ("Rain coat", "Safety", "pcs"),
    ("Ricotta Bags (50*100)", "Packaging", "pcs"),
    ("Roule / Parmesan Bags", "Packaging", "pcs"),
    ("Small crackers bag (60*100)", "Packaging", "pcs"),
    ("Soft Broom wooden handle", "Equipment", "pcs"),
    ("Soft Brush with Handle", "Equipment", "pcs"),
    ("Sparkling wine (sparkling steen)", "Ingredients", "pcs"),
    ("White Raisins Kg", "Ingredients", "kg"),
    ("shoe cover", "Safety", "pcs"),
]


def main():
    supabase_client = init_supabase_admin()
    if not supabase_client:
        print("Could not connect to Supabase with service role.")
        return

    existing = {i['item_name'] for i in get_all_items(active_only=False, supabase_client=supabase_client)}

    added, skipped = 0, 0
    for item_name, category, unit in MISSING_ITEMS:
        if item_name in existing:
            print(f"SKIP (already exists): {item_name}")
            skipped += 1
            continue
        try:
            create_item(
                item_name=item_name, item_category=category, unit_of_measure=unit,
                unit_price=0.0, reorder_level=0.0, created_by="migration-script",
                supabase_client=supabase_client,
            )
            print(f"ADDED: {item_name}  ({category}, {unit})")
            added += 1
        except Exception as e:
            print(f"FAILED: {item_name} -- {e}")

    print(f"\nDone. Added {added}, skipped {skipped} (already present).")
    print("Review categories/units/prices via the Inventory tab's Add/Edit Item form.")


if __name__ == "__main__":
    main()
