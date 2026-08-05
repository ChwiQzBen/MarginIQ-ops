"""
app/core/dry_ice_data_access.py
================================
Dual-backend (Supabase primary / SQLite fallback) persistence for the
Dry Ice module — extracted out of main.py, mirroring the pattern already
used for Cheese Production in cheese_data_access.py (try Supabase first,
fall back to SQLite, never let a DB hiccup crash the UI).

This module owns ONLY persistence for the dry ice `transactions`,
`inventory`, and `historical_orders` tables. UI, session_state
bookkeeping, and the report/forecast pipeline all stay in main.py — this
file just reads/writes the DB.

Usage from main.py:

    from app.core.dry_ice_data_access import (
        USE_SUPABASE, init_db, fix_order_date, seed_historical_data,
        add_transaction_to_db, get_transactions_from_db,
        get_current_stock_from_db, update_current_stock_in_db,
        get_historical_orders_from_db, get_period_from_date,
        clear_transactions_from_db,
    )

Note on init_supabase(): it stays defined in main.py because it's shared
across all three modes (All Items, BCPOS, Dry Ice), not dry-ice-specific.
Functions here that need a Supabase client do a lazy
`from app.main import init_supabase` inside the function body — exactly
like cheese_data_access.py's get_weighted_milk_cost_for_date() already
does — which avoids a circular import at module-load time (main.py
imports this module; this module only reaches back into main.py inside a
function call, after both modules have already finished loading).
"""

from __future__ import annotations
from datetime import datetime
import os
import sqlite3
import pandas as pd
import streamlit as st

from core.error_handling import (
    logger,
    safe_operation,
    safe_db_operation,
    validate_quantity,
    validate_date,
    validate_stock_sufficient,
    DatabaseError,
    ValidationError,
)
from core.security import AuthManager, AuditLogger, require_auth, require_permission, require_role
from core.advanced_security import rate_limited

USE_SUPABASE = True
DATABASE_FILE = 'dry_ice.db'
BAD_DATE = '2_024-09-26'
GOOD_DATE = '2024-09-26'


def fix_order_date():
    """Finds and fixes the incorrect date in both Supabase and SQLite."""

    # Fix in Supabase first
    if USE_SUPABASE:
        from app.core.supabase_client import init_supabase
        supabase = init_supabase()
        if supabase:
            try:
                result = supabase.table('historical_orders')\
                    .select('id')\
                    .eq('date', BAD_DATE)\
                    .execute()

                if result.data:
                    for row in result.data:
                        supabase.table('historical_orders')\
                            .update({'date': GOOD_DATE})\
                            .eq('id', row['id'])\
                            .execute()
                    print(f"Supabase: Fixed {len(result.data)} row(s) from '{BAD_DATE}' to '{GOOD_DATE}'.")
                else:
                    print(f"Supabase: No bad date found. Already clean.")
            except Exception as e:
                print(f"Supabase fix_order_date error: {e}")

    # Fix in SQLite as well
    if not os.path.exists(DATABASE_FILE):
        return

    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()

    try:
        c.execute("SELECT id FROM historical_orders WHERE date = ?", (BAD_DATE,))
        record = c.fetchone()

        if not record:
            print(f"SQLite: No bad date found. Already clean.")
            conn.close()
            return

        c.execute("UPDATE historical_orders SET date = ? WHERE date = ?", (GOOD_DATE, BAD_DATE))
        conn.commit()
        print(f"SQLite: Fixed {c.rowcount} row(s) from '{BAD_DATE}' to '{GOOD_DATE}'.")

    except sqlite3.Error as e:
        print(f"SQLite fix_order_date error: {e}")
        conn.rollback()
    finally:
        conn.close()


def init_db():
    """Initialize database (Supabase or SQLite). Local SQLite schema is
    ALWAYS created, even when Supabase is reachable — every write function
    in this module falls back to SQLite on a transient Supabase failure,
    so the fallback tables must already exist before that ever happens,
    not just when Supabase is unreachable at startup."""
    if USE_SUPABASE:
        from app.core.supabase_client import init_supabase
        if init_supabase():
            st.toast("☁️ Connected to cloud database", icon="✅")

    # Always ensure local SQLite fallback schema exists
    conn = sqlite3.connect('dry_ice.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  date TEXT NOT NULL,
                  type TEXT NOT NULL,
                  quantity REAL NOT NULL,
                  item TEXT NOT NULL,
                  description TEXT,
                  notes TEXT,
                  analysis_period TEXT)''')

    c.execute("PRAGMA table_info(transactions)")
    if 'analysis_period' not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE transactions ADD COLUMN analysis_period TEXT")

    c.execute('''CREATE TABLE IF NOT EXISTS inventory
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  date TEXT NOT NULL,
                  stock_level REAL NOT NULL,
                  transaction_id INTEGER,
                  FOREIGN KEY(transaction_id) REFERENCES transactions(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS historical_orders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  date TEXT NOT NULL,
                  order_quantity REAL NOT NULL,
                  analysis_period TEXT)''')

    c.execute("PRAGMA table_info(historical_orders)")
    if 'analysis_period' not in [col[1] for col in c.fetchall()]:
        c.execute("ALTER TABLE historical_orders ADD COLUMN analysis_period TEXT")

    conn.commit()
    conn.close()


# ============================================================
# CLEAR TRANSACTIONS - ADMIN ONLY WITH AUDIT LOGGING
@rate_limited(max_calls=2, period=300)  # 2 clears per 5 minutes
@require_role('admin')
def clear_transactions_from_db():
    """
    Permanently delete all records from the transactions, inventory,
    AND historical_orders tables to perform a full reset.

    🔐 This function is restricted to ADMIN users only.
    🔐 Rate limited to 2 calls per 5 minutes to prevent accidental deletion.
    """
    # ============================================================
    # 🔐 AUDIT LOGGING
    # ============================================================
    audit = AuditLogger()
    auth = AuthManager()

    audit.log(
        action='CLEAR_ALL_TRANSACTIONS',
        details="User initiated full data deletion",
        user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
    )

    conn = sqlite3.connect('dry_ice.db')
    c = conn.cursor()

    try:
        # Use DELETE to empty the tables. This is a permanent action.
        c.execute('DELETE FROM transactions')
        c.execute('DELETE FROM inventory')
        c.execute('DELETE FROM historical_orders') # --- ADD THIS LINE ---

        # Good practice: Reset the auto-increment counter for the primary keys
        c.execute('DELETE FROM sqlite_sequence WHERE name="transactions"')
        c.execute('DELETE FROM sqlite_sequence WHERE name="inventory"')
        c.execute('DELETE FROM sqlite_sequence WHERE name="historical_orders"') # --- AND THIS LINE ---

        conn.commit()

        # ============================================================
        # 🔐 AUDIT LOGGING - SUCCESS
        # ============================================================
        audit.log(
            action='CLEAR_ALL_TRANSACTIONS_SUCCESS',
            details="All transactions, inventory, and historical orders cleared successfully",
            user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
        )

        # The success message is now part of the button logic, so it's okay to remove it here

    except sqlite3.Error as e:
        # ============================================================
        # 🔐 AUDIT LOGGING - ERROR
        # ============================================================
        audit.log(
            action='CLEAR_ALL_TRANSACTIONS_ERROR',
            details=f"Error: {str(e)}",
            user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
        )
        st.error(f"Database error while clearing transactions: {e}")
    finally:
        conn.close()


# ============================================================
# 🔐 SECURE ADD TRANSACTION FUNCTION WITH RATE LIMITING
# ============================================================

@rate_limited(max_calls=10, period=60)  # 10 receipts per minute
@require_auth
@require_permission('record_receipt')
@safe_operation(error_message="Failed to add transaction")
def add_transaction_to_db(transaction_type, quantity, description, date, period):
    """
    Add transaction to Supabase or SQLite database with comprehensive error handling.

    Args:
        transaction_type: 'usage' or 'receipt'
        quantity: Quantity in kg
        description: Transaction description
        date: Transaction date
        period: Analysis period (e.g., '2024/2025')

    Returns:
        transaction_id: ID of the created transaction

    Raises:
        ValidationError: If input validation fails
        DatabaseError: If database operation fails

    🔐 This function requires authentication and 'record_receipt' permission.
    🔐 Rate limited to 10 calls per minute to prevent abuse.
    """

    # ============================================================
    # 🔐 AUDIT LOGGING
    # ============================================================
    audit = AuditLogger()
    auth = AuthManager()

    audit.log(
        action='RECORD_RECEIPT',
        details=f"Transaction: {transaction_type}, Quantity: {quantity}kg, Date: {date}, Period: {period}",
        user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
    )

    # ============================================================
    # STEP 1: VALIDATE INPUTS
    # ============================================================
    # Validate quantity
    is_valid, msg = validate_quantity(quantity, min_qty=0, max_qty=100000, allow_zero=False)
    if not is_valid:
        raise ValidationError(msg)

    # Validate date
    is_valid, msg = validate_date(date, allow_future=False)
    if not is_valid:
        raise ValidationError(msg)

    # Validate transaction type
    if transaction_type not in ['usage', 'receipt']:
        raise ValidationError(f"Invalid transaction type: {transaction_type}")

    logger.info(f"Processing {transaction_type} transaction: {quantity} kg on {date}")


    # ============================================================
    # STEP 2: TRY SUPABASE FIRST (if enabled)
    # ============================================================
    if USE_SUPABASE:
        try:
            from app.core.supabase_client import init_supabase
            supabase = init_supabase()
            if supabase:
                logger.info("Attempting Supabase transaction...")

                # Get current stock from Supabase
                current_stock_response = supabase.table('inventory')\
                    .select('stock_level')\
                    .order('date', desc=True)\
                    .limit(1)\
                    .execute()

                current_stock = current_stock_response.data[0]['stock_level'] if current_stock_response.data else 0
                logger.debug(f"Current stock (Supabase): {current_stock} kg")

                # Calculate new stock
                if transaction_type == 'usage':
                    new_stock = current_stock - quantity
                    # Validate stock sufficiency
                    is_valid, msg = validate_stock_sufficient(quantity, current_stock)
                    if not is_valid:
                        raise ValidationError(msg)
                else:  # receipt
                    new_stock = current_stock + quantity

                # Insert transaction
                transaction_data = {
                    'date': date.isoformat(),
                    'type': transaction_type,
                    'quantity': quantity,
                    'item': 'Dry Ice',
                    'description': description,
                    'notes': f"{description} - {quantity} kg",
                    'analysis_period': period
                }

                transaction_result = supabase.table('transactions').insert(transaction_data).execute()
                transaction_id = transaction_result.data[0]['id']
                logger.info(f"Supabase transaction created: {transaction_id}")

                # Insert inventory record
                inventory_data = {
                    'date': date.isoformat(),
                    'stock_level': new_stock,
                    'transaction_id': transaction_id
                }
                supabase.table('inventory').insert(inventory_data).execute()
                logger.debug(f"Inventory updated (Supabase): {new_stock} kg")

                # If receipt, add to historical orders
                if transaction_type == 'receipt':
                    order_data = {
                        'date': date.isoformat(),
                        'order_quantity': quantity,
                        'analysis_period': period
                    }
                    supabase.table('historical_orders').insert(order_data).execute()
                    logger.info(f"Historical order recorded (Supabase): {quantity} kg")

                # ============================================================
                # 🔐 AUDIT LOGGING - SUCCESS
                # ============================================================
                audit.log(
                    action='RECORD_RECEIPT_SUCCESS',
                    details=f"Transaction ID: {transaction_id}, New stock: {new_stock}kg",
                    user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
                )

                return transaction_id

        except ValidationError:
            # Re-raise validation errors
            raise

        except Exception as e:
            logger.warning(f"Supabase transaction failed: {e}. Falling back to SQLite.")
            # Fall through to SQLite


    # ============================================================
    # STEP 3: FALLBACK TO SQLITE (with safe operation)
    # ============================================================
    def sqlite_operation():
        """Execute SQLite transaction with proper error handling."""
        conn = None
        try:
            conn = sqlite3.connect('dry_ice.db')
            c = conn.cursor()
            logger.debug("SQLite connection established")

            # Get current stock from SQLite
            c.execute('''SELECT stock_level FROM inventory ORDER BY date DESC LIMIT 1''')
            result = c.fetchone()
            current_stock = result[0] if result else 0
            logger.debug(f"Current stock (SQLite): {current_stock} kg")

            # Calculate new stock
            if transaction_type == 'usage':
                new_stock = current_stock - quantity
                # Validate stock sufficiency
                is_valid, msg = validate_stock_sufficient(quantity, current_stock)
                if not is_valid:
                    raise ValidationError(msg)
            else:  # receipt
                new_stock = current_stock + quantity

            # Insert transaction
            transaction = {
                'date': date.isoformat(),
                'type': transaction_type,
                'quantity': quantity,
                'item': 'Dry Ice',
                'description': description,
                'notes': f"{description} - {quantity} kg",
                'analysis_period': period
            }

            c.execute('''INSERT INTO transactions
                         (date, type, quantity, item, description, notes, analysis_period)
                         VALUES (:date, :type, :quantity, :item, :description, :notes, :analysis_period)''',
                      transaction)

            transaction_id = c.lastrowid
            logger.debug(f"SQLite transaction created: {transaction_id}")

            # Insert inventory record
            if transaction_type == 'usage':
                c.execute('''INSERT INTO inventory (date, stock_level, transaction_id)
                             VALUES (?, ?, ?)''',
                          (date.isoformat(), new_stock, transaction_id))
            elif transaction_type == 'receipt':
                c.execute('''INSERT INTO inventory (date, stock_level, transaction_id)
                             VALUES (?, ?, ?)''',
                          (date.isoformat(), new_stock, transaction_id))

                # Add to historical orders for receipts
                c.execute('''INSERT INTO historical_orders (date, order_quantity, analysis_period)
                             VALUES (?, ?, ?)''',
                          (date.isoformat(), quantity, period))
                logger.debug(f"Historical order recorded (SQLite): {quantity} kg")

            conn.commit()
            logger.info(f"SQLite transaction completed: {transaction_id}")

            # ============================================================
            # 🔐 AUDIT LOGGING - SQLITE SUCCESS
            # ============================================================
            audit.log(
                action='RECORD_RECEIPT_SQLITE',
                details=f"Transaction ID: {transaction_id}, New stock: {new_stock}kg",
                user=auth.current_user['email'] if auth.is_authenticated else 'SYSTEM'
            )

            return transaction_id

        except sqlite3.IntegrityError as e:
            logger.error(f"SQLite integrity error: {e}")
            if conn:
                conn.rollback()
            raise DatabaseError(f"Data integrity error: {e}")

        except sqlite3.OperationalError as e:
            logger.error(f"SQLite operational error: {e}")
            if conn:
                conn.rollback()
            raise DatabaseError(f"Database operation error: {e}")

        except Exception as e:
            logger.error(f"SQLite transaction error: {e}")
            if conn:
                conn.rollback()
            raise

        finally:
            if conn:
                conn.close()
                logger.debug("SQLite connection closed")

    # Execute with safe database operation
    return safe_db_operation(
        sqlite_operation,
        fallback_value=None,
        show_error=True,
        error_title="Transaction Failed"
    )


@st.cache_data(ttl=300)
def get_transactions_from_db(period):
    """Retrieve transactions from Supabase or SQLite"""

    # Try Supabase first
    if USE_SUPABASE:
        from app.core.supabase_client import init_supabase
        supabase = init_supabase()
        if supabase:
            try:
                response = supabase.table('transactions')\
                    .select('date, type, quantity, item, description, notes')\
                    .eq('analysis_period', period)\
                    .order('date', desc=True)\
                    .execute()

                transaction_list = []
                for t in response.data:
                    transaction_list.append({
                        'date': datetime.fromisoformat(t['date']),
                        'type': t['type'],
                        'quantity': t['quantity'],
                        'item': t['item'],
                        'description': t['description'],
                        'notes': t['notes']
                    })
                return transaction_list
            except Exception as e:
                st.error(f"Supabase error: {e}. Falling back to SQLite.")

    # Fallback to SQLite
    conn = sqlite3.connect('dry_ice.db')
    c = conn.cursor()

    c.execute('''SELECT date, type, quantity, item, description, notes
                 FROM transactions WHERE analysis_period = ? ORDER BY date DESC''', (period,))
    transactions = c.fetchall()

    conn.close()

    transaction_list = []
    for t in transactions:
        transaction_list.append({
            'date': datetime.fromisoformat(t[0]),
            'type': t[1], 'quantity': t[2], 'item': t[3],
            'description': t[4], 'notes': t[5]
        })

    return transaction_list


@st.cache_data(ttl=30)
def get_current_stock_from_db():
    """Get current stock from Supabase or SQLite"""

    # Try Supabase first
    if USE_SUPABASE:
        from app.core.supabase_client import init_supabase
        supabase = init_supabase()
        if supabase:
            try:
                response = supabase.table('inventory')\
                    .select('stock_level')\
                    .order('date', desc=True)\
                    .limit(1)\
                    .execute()

                return response.data[0]['stock_level'] if response.data else 0
            except Exception as e:
                st.error(f"Supabase error: {e}. Falling back to SQLite.")

    # Fallback to SQLite
    conn = sqlite3.connect('dry_ice.db')
    c = conn.cursor()

    c.execute('''SELECT stock_level FROM inventory ORDER BY date DESC LIMIT 1''')
    result = c.fetchone()

    conn.close()

    return result[0] if result else 0


def update_current_stock_in_db(new_stock, date):
    """Update the current stock level in the database (Supabase or SQLite)"""

    # Try Supabase first if enabled
    if USE_SUPABASE:
        from app.core.supabase_client import init_supabase
        supabase = init_supabase()
        if supabase:
            try:
                # Handle date formatting
                if hasattr(date, 'isoformat'):
                    date_str = date.isoformat()
                else:
                    date_str = str(date)

                # Insert new inventory record
                inventory_data = {
                    'date': date_str,
                    'stock_level': float(new_stock),
                    'transaction_id': None  # Direct update, no associated transaction
                }
                supabase.table('inventory').insert(inventory_data).execute()
                print(f"Stock updated in Supabase: {new_stock} kg on {date_str}")
                get_current_stock_from_db.clear()
                return
            except Exception as e:
                print(f"Supabase error updating stock: {e}. Falling back to SQLite.")
                # Fall through to SQLite

    # Fallback to SQLite
    conn = sqlite3.connect('dry_ice.db')
    c = conn.cursor()

    # Handle date formatting for SQLite
    if hasattr(date, 'isoformat'):
        date_str = date.isoformat()
    else:
        date_str = str(date)

    c.execute('''INSERT INTO inventory (date, stock_level)
                 VALUES (?, ?)''',
              (date_str, float(new_stock)))

    conn.commit()
    conn.close()
    print(f"Stock updated in SQLite: {new_stock} kg on {date_str}")


def seed_historical_data():
    """
    Seed the database with historical order data if empty, and run a one-time
    update to tag any existing untagged records.
    """
    conn = sqlite3.connect('dry_ice.db')
    c = conn.cursor()

    # Check if the historical_orders table exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='historical_orders'")
    table_exists = c.fetchone()

    if not table_exists:
        print("historical_orders table doesn't exist yet. Creating it...")
        # Create the table if it doesn't exist
        c.execute('''CREATE TABLE IF NOT EXISTS historical_orders
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      date TEXT NOT NULL,
                      order_quantity REAL NOT NULL,
                      analysis_period TEXT)''')
        conn.commit()
        conn.close()
        return

    # --- Part 1: Seed data only if the table is completely empty ---
    c.execute('''SELECT COUNT(*) FROM historical_orders''')
    count = c.fetchone()[0]

    if count == 0:
        print("Database is empty. Seeding historical data for 2024/2025...")
        # This historical data is based on the list you provided, containing 151 orders.
        base_historical_data = [
            ('2025-06-30', 450.00), ('2025-06-27', 300.00), ('2025-06-26', 150.00),
            ('2025-06-26', 300.00), ('2025-06-23', 450.00), ('2025-06-20', 300.00),
            ('2025-06-18', 300.00), ('2025-06-16', 450.00), ('2025-06-12', 150.00),
            ('2025-06-12', 300.00), ('2025-06-09', 450.00), ('2025-06-05', 300.00),
            ('2025-06-05', 150.00), ('2025-06-03', 450.00), ('2025-05-30', 150.00),
            ('2025-05-26', 450.00), ('2025-05-23', 450.00), ('2025-05-28', 300.00),
            ('2025-05-21', 300.00), ('2025-05-19', 450.00), ('2025-05-07', 300.00),
            ('2025-05-09', 300.00), ('2025-05-14', 300.00), ('2025-05-12', 450.00),
            ('2025-04-28', 300.00), ('2025-05-16', 150.00), ('2025-05-02', 300.00),
            ('2025-05-05', 450.00), ('2025-04-30', 150.00), ('2025-04-30', 300.00),
            ('2025-04-17', 150.00), ('2025-04-14', 450.00), ('2025-04-25', 300.00),
            ('2025-04-24', 450.00), ('2025-04-22', 450.00), ('2025-04-11', 150.00),
            ('2025-04-07', 150.00), ('2025-04-04', 450.00), ('2025-04-09', 300.00),
            ('2025-04-07', 300.00), ('2025-04-02', 300.00), ('2025-04-01', 450.00),
            ('2025-03-28', 150.00), ('2025-03-26', 300.00), ('2025-03-21', 150.00),
            ('2025-03-24', 450.00), ('2025-03-19', 300.00), ('2025-03-17', 450.00),
            ('2025-03-14', 150.00), ('2025-03-12', 300.00), ('2025-03-07', 150.00),
            ('2025-03-07', 150.00), ('2025-03-05', 300.00), ('2025-03-10', 450.00),
            ('2025-03-03', 450.00), ('2025-02-28', 150.00), ('2025-02-26', 300.00),
            ('2025-02-24', 450.00), ('2025-02-21', 150.00), ('2025-02-17', 150.00),
            ('2025-02-17', 300.00), ('2025-02-19', 450.00), ('2025-02-14', 150.00),
            ('2025-02-12', 450.00), ('2025-02-10', 450.00), ('2025-02-05', 450.00),
            ('2025-02-03', 450.00), ('2025-01-27', 450.00), ('2025-01-29', 450.00),
            ('2025-01-22', 450.00), ('2025-01-16', 300.00), ('2025-01-20', 450.00),
            ('2025-01-10', 300.00), ('2025-01-13', 450.00), ('2025-01-08', 300.00),
            ('2025-01-06', 450.00), ('2025-01-02', 300.00), ('2024-12-31', 450.00),
            ('2024-12-31', 150.00), ('2024-12-31', 300.00), ('2024-12-23', 450.00),
            ('2024-12-20', 300.00), ('2024-12-18', 300.00), ('2024-12-16', 450.00),
            ('2024-12-13', 300.00), ('2024-12-01', 450.00), ('2024-12-11', 300.00),
            ('2024-12-09', 450.00), ('2024-12-05', 450.00), ('2024-12-06', 300.00),
            ('2024-12-02', 450.01), ('2024-11-28', 300.00), ('2024-11-25', 450.00),
            ('2024-11-22', 300.00), ('2024-11-18', 450.00), ('2024-11-21', 600.00),
            ('2024-11-14', 450.01), ('2024-11-14', 300.00), ('2024-11-11', 450.01),
            ('2024-11-07', 300.00), ('2024-10-31', 450.00), ('2024-10-28', 450.01),
            ('2024-10-22', 450.00), ('2024-10-24', 300.00), ('2024-10-01', 450.00),
            ('2024-10-01', 60.00), ('2024-10-01', 450.00), ('2024-10-04', 150.00),
            ('2024-10-11', 450.00), ('2024-10-14', 450.00), ('2024-10-17', 450.00),
            ('2024-10-03', 300.00), ('2024-09-23', 450.00), ('2024-09-26', 300.00),
            ('2024-09-13', 150.00), ('2024-09-19', 300.00), ('2024-09-16', 450.00),
            ('2024-09-12', 300.00), ('2024-09-09', 450.01), ('2024-09-05', 300.00),
            ('2024-08-29', 450.00), ('2024-09-02', 450.00), ('2024-08-26', 450.00),
            ('2024-08-22', 300.00), ('2024-08-19', 450.00), ('2024-08-15', 300.00),
            ('2024-08-13', 450.00), ('2024-08-08', 300.00), ('2024-08-05', 450.00),
            ('2024-08-02', 150.00), ('2024-08-01', 300.00), ('2024-07-31', 150.00),
            ('2024-07-30', 300.00), ('2024-07-26', 150.00), ('2024-07-25', 300.00),
            ('2024-07-22', 450.00), ('2024-07-18', 300.00), ('2024-07-15', 450.00),
            ('2024-07-11', 300.00), ('2024-07-08', 450.00), ('2024-07-04', 300.00),
            ('2024-07-01', 450.00)
        ]

        historical_data_with_period = [
            (date, qty, '2024/2025') for date, qty in base_historical_data
        ]

        c.executemany('''INSERT INTO historical_orders (date, order_quantity, analysis_period)
                         VALUES (?, ?, ?)''', historical_data_with_period)

        conn.commit()

    # --- Part 2: Find and fix any old records that don't have a period tag ---
    c.execute("SELECT COUNT(*) FROM historical_orders WHERE analysis_period IS NULL")
    untagged_count = c.fetchone()[0]

    if untagged_count > 0:
        print(f"Found {untagged_count} untagged historical records. Updating them to the '2024/2025' period...")
        # This SQL command updates all rows where the 'analysis_period' column is empty
        c.execute("""
            UPDATE historical_orders
            SET analysis_period = '2024/2025'
            WHERE analysis_period IS NULL
        """)
        conn.commit()
        print("Database update complete.")

    conn.close()


def get_period_from_date(order_date):
    """
    Determines the correct financial period string (e.g., '2025/2026')
    based on a given date, assuming a fiscal year starts in July.

    Args:
        order_date (datetime.date or datetime.datetime): The date of the transaction.

    Returns:
        str: The corresponding analysis period string.
    """
    if order_date.month >= 7:
        # If it's July or later, the financial year starts in the current year.
        start_year = order_date.year
    else:
        # If it's June or earlier, the financial year started in the previous year.
        start_year = order_date.year - 1

    return f"{start_year}/{start_year + 1}"


@st.cache_data(ttl=300)
def get_historical_orders_from_db(period):
    """Retrieve historical orders from Supabase or SQLite"""

    # Try Supabase first
    if USE_SUPABASE:
        from app.core.supabase_client import init_supabase
        supabase = init_supabase()
        if supabase:
            try:
                response = supabase.table('historical_orders')\
                    .select('date, order_quantity, analysis_period')\
                    .eq('analysis_period', period)\
                    .order('date')\
                    .execute()

                if response.data:
                    # FIX: Convert list of dicts to DataFrame correctly
                    df = pd.DataFrame(response.data)
                    df = df.rename(columns={
                        'date': 'Date',
                        'order_quantity': 'Order_Quantity_kg'
                    })

                    # Convert date strings to datetime
                    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

                    # Check for invalid dates
                    invalid_rows_mask = df['Date'].isnull()
                    if invalid_rows_mask.any():
                        bad_data_df = df[invalid_rows_mask].copy()
                        st.warning(
                            f"⚠️ Found and ignored {len(bad_data_df)} row(s) with an invalid date format.",
                            icon="❗"
                        )
                        with st.expander("Click here to see the problematic row(s)"):
                            st.dataframe(bad_data_df, use_container_width=True)

                        df = df[~invalid_rows_mask].copy()

                    return df
            except Exception as e:
                st.error(f"Supabase error: {e}. Falling back to SQLite.")

    # Fallback to SQLite (your existing code)
    try:
        conn = sqlite3.connect('dry_ice.db')
        c = conn.cursor()

        # Check if table exists first
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='historical_orders'")
        if not c.fetchone():
            # Table doesn't exist, return empty DataFrame
            conn.close()
            return pd.DataFrame(columns=['Date', 'Order_Quantity_kg', 'analysis_period'])

        c.execute('''SELECT date, order_quantity, analysis_period FROM historical_orders
                     WHERE analysis_period = ? ORDER BY date''', (period,))
        orders = c.fetchall()
        conn.close()
    except sqlite3.OperationalError as e:
        # Table doesn't exist or other SQLite error
        print(f"SQLite error in get_historical_orders_from_db: {e}")
        return pd.DataFrame(columns=['Date', 'Order_Quantity_kg', 'analysis_period'])

    if orders:
        df = pd.DataFrame(orders, columns=['Date', 'Order_Quantity_kg', 'analysis_period'])
        original_dates = df['Date'].copy()
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

        invalid_rows_mask = df['Date'].isnull()

        if invalid_rows_mask.any():
            bad_data_df = pd.DataFrame({
                'Problematic_Date_String': original_dates[invalid_rows_mask],
                'Order_Quantity_kg': df.loc[invalid_rows_mask, 'Order_Quantity_kg'],
                'Analysis_Period': df.loc[invalid_rows_mask, 'analysis_period']
            })

            st.warning(
                f"⚠️ Found and ignored {len(bad_data_df)} row(s) with an invalid date format.",
                icon="❗"
            )
            with st.expander("Click here to see the problematic row(s)"):
                st.dataframe(bad_data_df, use_container_width=True)

            df.dropna(subset=['Date'], inplace=True)

        return df
    else:
        return pd.DataFrame(columns=['Date', 'Order_Quantity_kg', 'analysis_period'])