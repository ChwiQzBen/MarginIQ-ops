# app/core/google_sheet_reader.py
import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from datetime import datetime

from app.core.supplier_utils import derive_item_supplier_links_from_check_in

class GoogleSheetReader:
    def __init__(self):
        self.sheet_url = st.secrets.get("GOOGLE_SHEET_URL", "")
        self.client = None
        self.sheet = None
        self.authenticated = False
        
    def authenticate(self):
        """Authenticate with Google Sheets API"""
        try:
            # Read-only scope
            scope = [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive.readonly'
            ]
            
            # Load credentials from secrets
            if 'google_credentials' in st.secrets:
                creds_dict = dict(st.secrets["google_credentials"])
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            else:
                creds = ServiceAccountCredentials.from_json_keyfile_name(
                    'google-credentials.json', scope
                )
            
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open_by_url(self.sheet_url)
            self.authenticated = True
            return True
        except Exception as e:
            st.error(f"Authentication failed: {e}")
            return False
    
    def read_worksheet(self, worksheet_name):
        """Read any worksheet into a DataFrame"""
        if not self.authenticated:
            if not self.authenticate():
                return pd.DataFrame()
        
        try:
            worksheet = self.sheet.worksheet(worksheet_name)
            data = worksheet.get_all_records()
            df = pd.DataFrame(data)
            df = df.dropna(how='all')
            return df
        except Exception as e:
            st.error(f"Error reading '{worksheet_name}': {e}")
            return pd.DataFrame()
    
    def get_all_worksheets(self):
        """Get list of all worksheets"""
        if not self.authenticated:
            self.authenticate()
        if self.sheet:
            return [ws.title for ws in self.sheet.worksheets()]
        return []
    
    def get_stock_listing(self):
        """Read STOCK_LISTING"""
        return self.read_worksheet("STOCK_LISTING")
    
    def get_current_stock(self):
        """Read CURRENT_STOCK"""
        return self.read_worksheet("CURRENT_STOCK")
    
    def get_check_in(self):
        """Read CHECK_IN"""
        return self.read_worksheet("CHECK_IN")
    
    def get_check_out(self):
        """Read CHECK_OUT"""
        return self.read_worksheet("CHECK_OUT")

    def get_suppliers(self):
        """Read SUPPLIERS -- item/supplier reference data for JIT purchasing
        (lead time, MOQ, unit cost, reliability, preferred flag). This tab
        is OPTIONAL: most deployments won't have it, since suppliers live in
        app/data/suppliers.csv instead (see app/core/suppliers_data_access.py
        and jit_purchasing_ui.py, which merge both sources). A missing tab
        is a normal state here, so this checks the workbook's worksheet
        list first rather than going through read_worksheet(), whose
        generic st.error() would otherwise show a confusing "Error reading
        'SUPPLIERS': SUPPLIERS" banner for what's simply "not created yet".

        If you have edit rights and want to maintain supplier data in
        Sheets instead of/alongside the CSV, create this tab with columns
        ITEM_SERIAL, SUPPLIER_NAME, LEAD_TIME_DAYS, MIN_ORDER_QTY,
        UNIT_COST, RELIABILITY_SCORE, PREFERRED -- one row per item/supplier
        pair. Use get_item_supplier_links_from_check_in() to see which
        pairs already show up in delivery history.
        """
        if not self.authenticated:
            if not self.authenticate():
                return pd.DataFrame()
        try:
            titles = [ws.title for ws in self.sheet.worksheets()]
        except Exception:
            return pd.DataFrame()
        if "SUPPLIERS" not in titles:
            return pd.DataFrame()
        return self.read_worksheet("SUPPLIERS")

    def get_item_supplier_links_from_check_in(self):
        """Item/supplier pairs already implied by CHECK_IN history --
        delivery count and first/last delivery date only, NOT lead time
        (CHECK_IN has no order date, only delivery date). A starting list
        for populating the new SUPPLIERS tab; lead time, MOQ, cost, and
        reliability still need to be filled in by hand."""
        check_in_df = self.get_check_in()
        return derive_item_supplier_links_from_check_in(check_in_df)

    def get_unit_pricing(self):
        """Read UNIT PRICING"""
        return self.read_worksheet("UNIT PRICING")
    
    def get_stock_with_pricing(self):
        """Get stock listing with prices from UNIT PRICING tab"""
        stock_df = self.get_stock_listing()
        pricing_df = self.get_unit_pricing()
        
        if stock_df.empty:
            return stock_df
        
        # If no pricing data, return stock without prices
        if pricing_df.empty:
            stock_df['UNIT PRICE'] = None
            return stock_df
        
        # Clean column names
        pricing_df.columns = pricing_df.columns.str.strip()
        
        # Find the price column
        price_col = None
        for col in ['AVERAGE UNIT PRICE', 'UNIT PRICE', 'Price', 'Average Unit Price']:
            if col in pricing_df.columns:
                price_col = col
                break
        
        if not price_col:
            print(f"Warning: No price column found. Columns: {list(pricing_df.columns)}")
            stock_df['UNIT PRICE'] = None
            return stock_df
        
        # Find item name column in pricing
        item_col = None
        for col in ['ITEM_DESCRIPTION', 'ITEM_NAME', 'Description', 'Item']:
            if col in pricing_df.columns:
                item_col = col
                break
        
        if not item_col:
            print(f"Warning: No item column found in pricing. Columns: {list(pricing_df.columns)}")
            stock_df['UNIT PRICE'] = None
            return stock_df
        
        # Find item name column in stock
        stock_item_col = None
        for col in ['ITEM_NAME', 'ITEM_DESCRIPTION', 'Item Name']:
            if col in stock_df.columns:
                stock_item_col = col
                break
        
        if not stock_item_col:
            print(f"Warning: No item column found in stock. Columns: {list(stock_df.columns)}")
            stock_df['UNIT PRICE'] = None
            return stock_df
        
        # Create price lookup dictionary
        price_dict = {}
        for _, row in pricing_df.iterrows():
            item_name = str(row[item_col]).strip()
            price = row[price_col]
            if pd.notna(price) and price > 0:
                # If multiple prices for same item, keep the first one
                if item_name not in price_dict:
                    price_dict[item_name] = price
        
        # Add price column to stock
        stock_df['UNIT PRICE'] = stock_df[stock_item_col].map(price_dict)
        
        # Count how many got prices
        price_count = stock_df['UNIT PRICE'].notna().sum()
        print(f"✅ Matched {price_count} items with prices")
        
        return stock_df
    
    def get_inventory_summary(self):
        """Get combined inventory summary with pricing"""
        # Use the new method that includes pricing
        stock_df = self.get_stock_with_pricing()
        current_stock_df = self.get_current_stock()
        
        # Start with stock listing (already has prices)
        summary = stock_df.copy() if not stock_df.empty else pd.DataFrame()
        
        # Add current stock if available
        if not current_stock_df.empty and not summary.empty:
            # Try to find matching columns
            name_cols = ['ITEM_NAME', 'Item Name', 'Product', 'Item']
            found_col = None
            for col in name_cols:
                if col in summary.columns and col in current_stock_df.columns:
                    found_col = col
                    break
            
            if found_col:
                summary = summary.merge(
                    current_stock_df, 
                    on=found_col, 
                    how='left'
                )
        
        return summary
    
    def get_low_stock_items(self, threshold=None):
        """Get items with low stock levels"""
        stock_df = self.get_current_stock()
        if stock_df.empty:
            return pd.DataFrame()
        
        # Find stock column
        stock_col = None
        for col in stock_df.columns:
            if 'stock' in col.lower() or 'quantity' in col.lower():
                stock_col = col
                break
        
        if stock_col:
            if threshold is None:
                # Try to find reorder point column
                reorder_col = None
                for col in stock_df.columns:
                    if 'reorder' in col.lower() or 'reorder level' in col.lower():
                        reorder_col = col
                        break
                
                if reorder_col:
                    low_stock = stock_df[stock_df[stock_col] <= stock_df[reorder_col]]
                else:
                    # Use 10% of average as threshold
                    avg = stock_df[stock_col].mean()
                    low_stock = stock_df[stock_df[stock_col] <= avg * 0.1]
            else:
                low_stock = stock_df[stock_df[stock_col] <= threshold]
            
            return low_stock
        
        return pd.DataFrame()
    
    def get_category_summary(self):
        """Get summary by category"""
        stock_df = self.get_stock_listing()
        if stock_df.empty:
            return pd.DataFrame()
        
        # Find category column
        category_col = None
        for col in stock_df.columns:
            if 'category' in col.lower() or 'item_category' in col.lower():
                category_col = col
                break
        
        if category_col:
            summary = stock_df.groupby(category_col).size().reset_index(name='Count')
            return summary
        
        return pd.DataFrame()