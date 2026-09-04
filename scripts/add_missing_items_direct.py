"""
Alternative: Direct insert using service role key
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from supabase import create_client
from datetime import datetime

def get_service_client():
    """Create a Supabase client with the service role key"""
    try:
        from app.core.supabase_client import SUPABASE_URL
        SUPABASE_SERVICE_KEY = st.secrets.get("SUPABASE_SERVICE_KEY")
        if not SUPABASE_SERVICE_KEY:
            raise ValueError("SUPABASE_SERVICE_KEY not found in secrets")
        return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as e:
        print(f"❌ Could not create service client: {e}")
        return None

MISSING_ITEMS = [
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
    supabase = get_service_client()
    if not supabase:
        return

    added = 0
    for item_name, category, unit in MISSING_ITEMS:
        try:
            row = {
                "item_name": item_name,
                "item_category": category,
                "unit_of_measure": unit,
                "unit_price": 0.0,
                "reorder_level": 0.0,
                "seed_quantity": 0.0,
                "active": True,
                "created_by": "migration-script",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            supabase.table("item_master").insert(row).execute()
            print(f"ADDED: {item_name}")
            added += 1
        except Exception as e:
            print(f"FAILED: {item_name} -- {e}")

    print(f"\nDone. Added {added} items.")

if __name__ == "__main__":
    main()
