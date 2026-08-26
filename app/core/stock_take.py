"""
app/core/stock_take.py
========================
The inFlow-style Stock Take feature, extracted verbatim from main.py.
Operates only on inventory_items (passed in) and st.session_state, so it
lifted out cleanly with no circular-import dependency on main.py.

get_sample_inventory_data() is imported from app.core.visual_inventory
rather than duplicated here — see that module's docstring for why it lives
there.

Note: init_stock_take_session() is called defensively at the top of
stock_take_interface() (not just relied on from main()'s session_defaults
block) so this module is safe to call standalone. main.py's
session_defaults block already sets the same keys before this ever renders,
so that call site did not need to move — this is just a second, harmless
safety net using the same "only set if missing" pattern the function
already had.

Unblocks: app/core/all_items_ui.py's movement_tab4 ("📋 Stock Take" sub-tab
inside 📊 Stock Movements).
"""
from datetime import datetime
import streamlit as st
import pandas as pd
import uuid
import logging

from app.core.visual_inventory import get_sample_inventory_data

logger = logging.getLogger(__name__)


def _get_supabase_client():
    try:
        from app.core.supabase_client import init_supabase
        return init_supabase()
    except Exception:
        return None


def _get_team_members(supabase_client=None):
    """Real user names from the app's actual user list, not hardcoded
    placeholders -- was previously a fixed list of five made-up names
    (John Doe, Jane Smith, etc.) that never matched anyone real."""
    try:
        from core.advanced_security import UserManager
        users = UserManager(supabase_client=supabase_client).get_users()
        names = sorted({(u.get('name') or email) for email, u in users.items()})
        return ["Unassigned"] + names
    except Exception as e:
        logger.error(f"Could not load team members: {e}")
        return ["Unassigned"]


def load_persisted_stock_takes(supabase_client=None):
    """Loads stock_takes/count_sheets from Supabase into session_state,
    ONLY if session_state is currently empty -- covers a cold start
    after a restart without clobbering in-progress edits made earlier
    in this same running session."""
    if st.session_state.get('stock_takes'):
        return
    client = supabase_client or _get_supabase_client()
    if not client:
        return
    try:
        result = client.table("stock_take_state").select("*").eq("id", 1).execute()
        if result.data:
            row = result.data[0]
            st.session_state.stock_takes = row.get("stock_takes") or {}
            st.session_state.count_sheets = row.get("count_sheets") or {}
    except Exception as e:
        logger.error(f"Could not load persisted stock takes: {e}")
        st.warning(
            "⚠️ Could not load previous stock take history right now — starting with "
            "an empty view. If counts you expect to see don't reappear once this "
            "reconnects, they may not have saved — check with whoever ran them."
        )


def _persist_stock_takes(supabase_client=None):
    """Write-through save. A failed save now shows a visible one-time
    warning instead of only logging server-side -- this exact function
    was written to stop stock takes from silently vanishing, and until
    now it had the identical silent-failure pattern itself: every count/
    assign/enter/complete action could fail to save with zero on-screen
    indication."""
    client = supabase_client or _get_supabase_client()
    if not client:
        if not st.session_state.get('_stock_take_save_warned'):
            st.warning(
                "⚠️ No database connection — this change will NOT be saved permanently "
                "and will be lost if the app restarts."
            )
            st.session_state['_stock_take_save_warned'] = True
        return
    try:
        client.table("stock_take_state").upsert({
            "id": 1,
            "stock_takes": st.session_state.get('stock_takes', {}),
            "count_sheets": st.session_state.get('count_sheets', {}),
            "updated_at": datetime.now().isoformat(),
        }).execute()
        st.session_state.pop('_stock_take_save_warned', None)
    except Exception as e:
        logger.error(f"Could not persist stock takes: {e}")
        if not st.session_state.get('_stock_take_save_warned'):
            st.warning(
                "⚠️ Could not save this change — database error. It will be lost if "
                "the app restarts."
            )
            st.session_state['_stock_take_save_warned'] = True


def init_stock_take_session():
    """Initialize stock take session state variables (safely)"""
    if 'stock_takes' not in st.session_state:
        st.session_state.stock_takes = {}
    if 'active_count_id' not in st.session_state:
        st.session_state.active_count_id = None
    if 'count_sheets' not in st.session_state:
        st.session_state.count_sheets = {}
    if 'count_assignments' not in st.session_state:
        st.session_state.count_assignments = {}
    if 'stock_take_menu' not in st.session_state:
        st.session_state.stock_take_menu = "📊 Dashboard"
    if 'stock_take_selected_menu' not in st.session_state:
        st.session_state.stock_take_selected_menu = "📊 Dashboard"
    if 'stock_take_inventory' not in st.session_state:
        st.session_state.stock_take_inventory = {}
    if 'stock_take_action' not in st.session_state:
        st.session_state.stock_take_action = None


def generate_count_id():
    """Generate a unique count ID"""
    return f"CT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


def get_status_color(status):
    """Get color for status badge"""
    colors = {
        'Open': '#ffc107',
        'In Progress': '#17a2b8',
        'Ready for Review': '#ff9800',
        'Completed': '#28a745',
        'Pending': '#6c757d',
        'Counted': '#28a745'
    }
    return colors.get(status, '#6c757d')


def get_progress_color(progress):
    """Get color based on progress percentage"""
    if progress >= 0.8:
        return '#28a745'
    elif progress >= 0.5:
        return '#ffc107'
    else:
        return '#dc3545'


def create_stock_count(inventory_items, count_name, count_type="Physical", warehouse="All", supabase_client=None):
    """
    Create a new stock count (like inFlow's stock count creation)
    """
    count_id = generate_count_id()
    
    # Create a snapshot of current inventory
    snapshot = {}
    for item, details in inventory_items.items():
        snapshot[item] = {
            'system_qty': details.get('stock', 0),
            'unit': details.get('unit', 'kg'),
            'category': details.get('category', 'Uncategorized'),
            'reorder': details.get('reorder', 0),
            'max': details.get('max', 0),
            'counted_qty': 0,
            'status': 'Pending',
            'variance': 0,
            'notes': ''
        }
    
    st.session_state.stock_takes[count_id] = {
        'id': count_id,
        'name': count_name,
        'type': count_type,
        'warehouse': warehouse,
        'status': 'Open',
        'created': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'created_by': 'Current User',
        'items': snapshot,
        'sheets': [],
        'progress': {
            'total': len(snapshot),
            'counted': 0,
            'pending': len(snapshot)
        }
    }
    _persist_stock_takes(supabase_client)

    return count_id


def split_count_into_sheets(count_id, num_sheets=2, supabase_client=None):
    """
    Split a stock count into multiple sheets (inFlow feature)
    """
    if count_id not in st.session_state.stock_takes:
        return None
    
    count = st.session_state.stock_takes[count_id]
    items = list(count['items'].keys())
    
    if not items:
        return None
    
    # Split items evenly across sheets
    sheet_size = max(1, len(items) // num_sheets)
    sheets = []
    
    for i in range(num_sheets):
        start_idx = i * sheet_size
        end_idx = min((i + 1) * sheet_size, len(items))
        
        if start_idx >= len(items):
            break
            
        sheet_items = items[start_idx:end_idx]
        sheet_id = f"{count_id}-S{i+1:02d}"
        
        sheet = {
            'id': sheet_id,
            'name': f"Sheet {i+1}",
            'items': sheet_items,
            'assigned_to': None,
            'status': 'Pending',
            'counted_items': 0,
            'total_items': len(sheet_items)
        }
        
        sheets.append(sheet)
        st.session_state.count_sheets[sheet_id] = sheet
    
    # Also store as dictionary for easy lookup
    st.session_state.count_sheets.update({s['id']: s for s in sheets})
    count['sheets'] = [s['id'] for s in sheets]
    _persist_stock_takes(supabase_client)

    return sheets


def assign_sheet_to_user(sheet_id, user_name, supabase_client=None):
    """
    Assign a count sheet to a team member (inFlow feature)
    """
    if sheet_id in st.session_state.count_sheets:
        st.session_state.count_sheets[sheet_id]['assigned_to'] = user_name
        _persist_stock_takes(supabase_client)
        return True
    return False


def enter_count(count_id, item_name, counted_qty, sheet_id=None, notes="", persist=True, supabase_client=None):
    """
    Enter count for a specific item (inFlow style). persist=False lets a
    caller doing many of these in a batch skip a Supabase round-trip per
    item and persist once after the whole batch instead.
    """
    if count_id not in st.session_state.stock_takes:
        return False, "Count not found"
    
    count = st.session_state.stock_takes[count_id]
    
    if item_name not in count['items']:
        return False, "Item not in count"
    
    # Update the counted quantity
    count['items'][item_name]['counted_qty'] = counted_qty
    count['items'][item_name]['notes'] = notes
    count['items'][item_name]['status'] = 'Counted'
    
    # Calculate variance
    system_qty = count['items'][item_name]['system_qty']
    variance = counted_qty - system_qty
    count['items'][item_name]['variance'] = variance
    
    # Update progress
    count['progress']['counted'] = sum(1 for item in count['items'].values() if item['status'] == 'Counted')
    count['progress']['pending'] = count['progress']['total'] - count['progress']['counted']
    
    # Update sheet progress if sheet_id provided
    if sheet_id and sheet_id in st.session_state.count_sheets:
        sheet = st.session_state.count_sheets[sheet_id]
        sheet['counted_items'] = sum(1 for item_name in sheet['items'] 
                                     if count['items'][item_name]['status'] == 'Counted')
        
        if sheet['counted_items'] >= sheet['total_items']:
            sheet['status'] = 'Complete'
    
    # Check if all items are counted
    if count['progress']['counted'] >= count['progress']['total']:
        count['status'] = 'Ready for Review'
    else:
        count['status'] = 'In Progress'

    if persist:
        _persist_stock_takes(supabase_client)
    return True, "Count recorded successfully"


def get_count_summary(count_id):
    """
    Get summary statistics for a count
    """
    if count_id not in st.session_state.stock_takes:
        return None
    
    count = st.session_state.stock_takes[count_id]
    items = count['items']
    
    total_items = len(items)
    counted_items = sum(1 for item in items.values() if item['status'] == 'Counted')
    pending_items = total_items - counted_items
    
    # Calculate discrepancies
    discrepancies = []
    for item_name, details in items.items():
        if details['status'] == 'Counted' and details['variance'] != 0:
            discrepancies.append({
                'item': item_name,
                'system_qty': details['system_qty'],
                'counted_qty': details['counted_qty'],
                'variance': details['variance'],
                'unit': details['unit']
            })
    
    return {
        'total_items': total_items,
        'counted_items': counted_items,
        'pending_items': pending_items,
        'completion_rate': (counted_items / total_items * 100) if total_items > 0 else 0,
        'discrepancies': discrepancies,
        'has_discrepancies': len(discrepancies) > 0
    }


def complete_and_adjust(count_id, supabase_client=None):
    """
    Complete the count and adjust inventory (inFlow's "Complete & Adjust" feature)
    """
    if count_id not in st.session_state.stock_takes:
        return False, "Count not found"
    
    count = st.session_state.stock_takes[count_id]
    
    # Check if all items are counted
    if count['progress']['counted'] < count['progress']['total']:
        return False, f"Only {count['progress']['counted']} of {count['progress']['total']} items counted"
    
    # Calculate adjustments
    adjustments = []
    for item_name, details in count['items'].items():
        if details['variance'] != 0:
            adjustments.append({
                'item': item_name,
                'system_qty': details['system_qty'],
                'counted_qty': details['counted_qty'],
                'variance': details['variance'],
                'unit': details['unit']
            })
    
    # Update status
    count['status'] = 'Completed'
    count['completed'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    count['adjustments'] = adjustments
    _persist_stock_takes(supabase_client)

    return True, f"Count completed with {len(adjustments)} adjustments"


def get_count_history():
    """
    Get history of completed counts
    """
    history = []
    for count_id, count in st.session_state.stock_takes.items():
        if count['status'] == 'Completed':
            history.append({
                'id': count_id,
                'name': count['name'],
                'type': count['type'],
                'completed': count.get('completed', ''),
                'adjustments': len(count.get('adjustments', []))
            })
    return sorted(history, key=lambda x: x['completed'], reverse=True)


def stock_take_dashboard():
    """
    Stock take dashboard showing overview (inFlow style)
    """
    st.markdown("""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 20px;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="
            font-size: 1.3rem;
            font-weight: 600;
            margin-bottom: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">
            📊 Stock Take Dashboard
        </div>
        <div style="color: #888; font-size: 13px;">
            Overview of all stock counts. Manage physical inventory and cycle counts.
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Summary stats
    total_counts = len(st.session_state.stock_takes)
    open_counts = sum(1 for c in st.session_state.stock_takes.values() if c['status'] in ['Open', 'In Progress', 'Ready for Review'])
    completed_counts = sum(1 for c in st.session_state.stock_takes.values() if c['status'] == 'Completed')
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📋 Total Counts", total_counts)
    with col2:
        st.metric("🟡 Active Counts", open_counts)
    with col3:
        st.metric("✅ Completed", completed_counts)
    with col4:
        avg_rate = 0
        if total_counts > 0:
            rates = []
            for count in st.session_state.stock_takes.values():
                if count['progress']['total'] > 0:
                    rates.append(count['progress']['counted'] / count['progress']['total'] * 100)
            avg_rate = sum(rates) / len(rates) if rates else 0
        st.metric("📊 Avg Completion", f"{avg_rate:.0f}%")
    
    # Recent counts
    st.markdown("---")
    st.markdown("#### Recent Counts")
    
    recent = list(st.session_state.stock_takes.values())[-5:]
    if recent:
        recent_data = []
        for count in reversed(recent):
            recent_data.append({
                'ID': count['id'],
                'Name': count['name'],
                'Location': count.get('warehouse', 'Unknown'),
                'Status': count['status'],
                'Progress': f"{count['progress']['counted']}/{count['progress']['total']}",
                'Created': count['created']
            })
        st.dataframe(pd.DataFrame(recent_data), use_container_width=True, hide_index=True)
    else:
        st.info("No stock counts created yet. Create your first count!")


def stock_take_interface(inventory_items, supabase_client=None):
    """
    Main stock take interface (inFlow style)
    """
    # Defensive init: main.py's session_defaults block already sets these
    # keys before this ever renders, but calling this here too makes the
    # module safe to use standalone (it only sets keys that are missing).
    init_stock_take_session()
    load_persisted_stock_takes(supabase_client)
    
    # Always sync session state with the freshly-passed inventory_items
    # when it has real content. Previously this only wrote on the FIRST
    # ever run ('not in session_state'), so once stock_take_inventory got
    # set to sample/stale data on any earlier run, it silently shadowed
    # every subsequent call forever — even after the real, correctly
    # filtered inventory_items started arriving on every rerun. Only fall
    # back to sample data when the passed-in inventory_items is itself
    # genuinely empty, not based on whatever session state happened to
    # hold from a previous run.
    if inventory_items:
        st.session_state.stock_take_inventory = inventory_items
    elif 'stock_take_inventory' not in st.session_state or not st.session_state.stock_take_inventory:
        st.session_state.stock_take_inventory = get_sample_inventory_data()
        
    # Use the selected menu from session state (set in main sidebar)
    selected_menu = st.session_state.get('stock_take_selected_menu', "📊 Dashboard")
    # ---- MAIN CONTENT ----
    if selected_menu == "📊 Dashboard":
        stock_take_dashboard()
    elif selected_menu == "📝 New Count":
        new_count_form(st.session_state.stock_take_inventory, supabase_client=supabase_client)
    elif selected_menu == "📋 Active Counts":
        active_counts_interface(st.session_state.stock_take_inventory, supabase_client=supabase_client)
    elif selected_menu == "📜 History":
        count_history_interface()


def new_count_form(inventory_items, supabase_client=None):
    """
    Form to create a new stock count (inFlow style)
    """
    st.markdown("""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 20px;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="
            font-size: 1.3rem;
            font-weight: 600;
            margin-bottom: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">
            📝 Create New Stock Count
        </div>
        <div style="color: #888; font-size: 13px;">
            Create a physical inventory count or cycle count. You can split into multiple sheets and assign to team members.
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        count_name = st.text_input(
            "Count Name",
            placeholder="e.g., Warehouse A - July 2024",
            key="count_name_input"
        )
        count_type = st.selectbox(
            "Count Type",
            ["Physical Inventory", "Cycle Count"],
            key="count_type_input"
        )
    
    with col2:
        from app.core.locations import COMPANY_LOCATIONS
        warehouse = st.selectbox(
            "Warehouse/Location",
            COMPANY_LOCATIONS,
            key="count_warehouse_input"
        )
        num_sheets = st.number_input(
            "Number of Sheets",
            min_value=1,
            max_value=10,
            value=2,
            help="Split count into multiple sheets for team assignments",
            key="num_sheets_input"
        )
    
    # Items to count
    st.markdown("---")
    st.markdown("#### 📦 Items to Count")
    
    items_df = pd.DataFrame([
        {
            'Item': item,
            'Current Stock': details.get('stock', 0),
            'Unit': details.get('unit', 'kg'),
            'Category': details.get('category', 'Uncategorized')
        }
        for item, details in inventory_items.items()
    ])
    
    if not items_df.empty:
        count_all = st.checkbox("Count all items", value=True, key="count_all_items")
        
        if not count_all:
            selected_items = st.multiselect(
                "Select items to count",
                options=items_df['Item'].tolist(),
                default=items_df['Item'].head(10).tolist(),
                key="selected_items_multiselect"
            )
        else:
            selected_items = items_df['Item'].tolist()
        
        st.caption(f"Selected {len(selected_items)} items")
        
        with st.expander("📋 View Selected Items"):
            selected_df = items_df[items_df['Item'].isin(selected_items)]
            st.dataframe(selected_df, use_container_width=True, hide_index=True)
    else:
        selected_items = []
        st.warning("No items available to count")
    
    # Create button
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        if st.button("🚀 Create Stock Count", type="primary", use_container_width=True):
            if not count_name:
                st.error("Please enter a count name")
            elif not selected_items:
                st.error("Please select items to count")
            else:
                # Create the count
                count_id = create_stock_count(
                    {item: inventory_items[item] for item in selected_items if item in inventory_items},
                    count_name,
                    count_type,
                    warehouse,
                    supabase_client=supabase_client,
                )
                
                # Split into sheets
                if num_sheets > 1:
                    sheets = split_count_into_sheets(count_id, num_sheets, supabase_client=supabase_client)
                    st.success(f"✅ Count '{count_name}' created with {len(sheets)} sheets! (Count ID: {count_id})")
                else:
                    st.success(f"✅ Count '{count_name}' created successfully! (Count ID: {count_id})")
                
                st.session_state.active_count_id = count_id
                # NOTE: this key is stock_take_SELECTED_menu -- the sidebar's
                # actual dispatch key. Every "navigate to Active Counts"
                # attempt in this file previously set the similarly-named
                # but entirely unread stock_take_menu instead, which is why
                # none of them actually navigated anywhere.
                st.session_state.stock_take_selected_menu = "📋 Active Counts"
                st.rerun()


def active_counts_interface(inventory_items, supabase_client=None):
    """
    Interface for active counts (inFlow style)
    """
    st.markdown("""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 20px;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="
            font-size: 1.3rem;
            font-weight: 600;
            margin-bottom: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">
            📋 Active Stock Counts
        </div>
        <div style="color: #888; font-size: 13px;">
            Manage and complete your stock counts. Track progress and assign sheets to team members.
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Filters
    col1, col2 = st.columns(2)
    with col1:
        status_filter = st.selectbox(
            "Filter by Status",
            ["All", "Open", "In Progress", "Ready for Review", "Completed"],
            key="count_status_filter"
        )
    with col2:
        search = st.text_input(
            "🔍 Search",
            placeholder="Search by count name or ID...",
            key="count_search"
        )
    
    # Display counts
    counts = []
    for count_id, count in st.session_state.stock_takes.items():
        if status_filter != "All" and count['status'] != status_filter:
            continue
        if search and search.lower() not in count['name'].lower() and search.lower() not in count_id.lower():
            continue
        counts.append(count)
    
    if not counts:
        st.info("No counts found matching your filters")
        return
    
    for count in counts:
        with st.container():
            st.markdown(f"""
            <div style="
                background: rgba(255,255,255,0.06);
                backdrop-filter: blur(8px);
                border-radius: 12px;
                padding: 16px 20px;
                margin: 10px 0;
                border: 1px solid rgba(255,255,255,0.08);
            ">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <div style="font-weight: 600; font-size: 16px;">{count['name']}</div>
                        <div style="font-size: 12px; color: #888;">
                            📍 {count.get('warehouse', 'Unknown')} | {count['type']} | Created: {count['created']}
                        </div>
                        <div style="font-size: 10px; color: #aaa; margin-top: 2px;">{count['id']}</div>
                    </div>
                    <div>
                        <span style="
                            background: {get_status_color(count['status'])};
                            color: white;
                            padding: 4px 12px;
                            border-radius: 20px;
                            font-size: 12px;
                            font-weight: 600;
                        ">
                            {count['status']}
                        </span>
                    </div>
                </div>
                <div style="margin-top: 10px;">
                    <div style="display: flex; gap: 20px; flex-wrap: wrap;">
                        <div>
                            <span style="color: #888; font-size: 12px;">Progress</span>
                            <div style="font-weight: 600;">{count['progress']['counted']} / {count['progress']['total']}</div>
                        </div>
                        <div style="flex: 1;">
                            <div style="margin: 4px 0; height: 6px; background: #eee; border-radius: 3px; overflow: hidden;">
                                <div style="
                                    width: {count['progress']['counted'] / count['progress']['total'] * 100 if count['progress']['total'] > 0 else 0}%;
                                    height: 6px;
                                    background: {get_progress_color(count['progress']['counted'] / count['progress']['total'] if count['progress']['total'] > 0 else 0)};
                                    border-radius: 3px;
                                    transition: width 0.6s ease;
                                "></div>
                            </div>
                        </div>
                        <div>
                            <span style="color: #888; font-size: 12px;">Sheets</span>
                            <div style="font-weight: 600;">{len(count['sheets'])}</div>
                        </div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # Action buttons -- these only SET which detail view to show;
            # they no longer RENDER it from inside their own narrow
            # column. See the block below, outside this columns() row.
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                if st.button("📊 View", key=f"view_{count['id']}", use_container_width=True):
                    st.session_state.active_count_id = count['id']
                    st.session_state.stock_take_action = 'view'
            
            with col2:
                if count['status'] not in ['Completed', 'Ready for Review']:
                    if st.button("📝 Enter Counts", key=f"enter_{count['id']}", use_container_width=True, type="primary"):
                        st.session_state.active_count_id = count['id']
                        st.session_state.stock_take_action = 'enter'
            
            with col3:
                if count['status'] == 'Ready for Review':
                    if st.button("✅ Complete & Adjust", key=f"complete_{count['id']}", use_container_width=True, type="primary"):
                        success, message = complete_and_adjust(count['id'], supabase_client=supabase_client)
                        if success:
                            st.success(message)
                            st.balloons()
                        else:
                            st.error(message)
            
            with col4:
                if count['status'] != 'Completed':
                    if st.button("👥 Assign Sheets", key=f"assign_{count['id']}", use_container_width=True):
                        st.session_state.active_count_id = count['id']
                        st.session_state.stock_take_action = 'assign'

            # Rendered full-width, OUTSIDE the button columns above --
            # previously view_count_detail / enter_counts_interface /
            # assign_sheets_interface were each called from directly
            # inside their own quarter-width st.columns() cell, which
            # squeezed everything inside them (including their own
            # nested st.columns() calls) into a sliver of the page. This
            # was the actual cause of the "squeezed, all vertical" look
            # on all three screens -- not any individual layout choice
            # inside those functions.
            if st.session_state.get('active_count_id') == count['id'] and st.session_state.get('stock_take_action'):
                st.markdown("---")
                action = st.session_state.stock_take_action
                if action == 'view':
                    view_count_detail(count['id'])
                elif action == 'enter':
                    enter_counts_interface(count['id'], supabase_client=supabase_client)
                elif action == 'assign':
                    assign_sheets_interface(count['id'], supabase_client=supabase_client)

            st.markdown("---")


def view_count_detail(count_id):
    """
    View detailed information about a count
    """
    if count_id not in st.session_state.stock_takes:
        st.error("Count not found")
        return
    
    count = st.session_state.stock_takes[count_id]
    
    st.markdown(f"""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 12px;
        padding: 16px;
        margin: 10px 0;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <div style="font-size: 18px; font-weight: 600;">{count['name']}</div>
                <div style="font-size: 13px; color: #888;">
                    📍 {count['warehouse']} | {count['type']} | Created: {count['created']}
                </div>
            </div>
            <div>
                <span style="
                    background: {get_status_color(count['status'])};
                    color: white;
                    padding: 4px 12px;
                    border-radius: 20px;
                    font-size: 12px;
                    font-weight: 600;
                ">
                    {count['status']}
                </span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Sheets
    if count['sheets']:
        st.markdown("#### 📋 Count Sheets")
        
        cols = st.columns(3)
        for idx, sheet_id in enumerate(count['sheets']):
            sheet = st.session_state.count_sheets.get(sheet_id, {})
            if not sheet:
                continue
            
            with cols[idx % 3]:
                st.markdown(f"""
                <div style="
                    background: rgba(255,255,255,0.05);
                    border-radius: 8px;
                    padding: 12px;
                    border: 1px solid rgba(255,255,255,0.05);
                ">
                    <div style="font-weight: 600;">{sheet.get('name', 'Unknown')}</div>
                    <div style="font-size: 12px; color: #888;">
                        Items: {sheet.get('counted_items', 0)}/{sheet.get('total_items', 0)}
                    </div>
                    <div style="font-size: 12px; color: #888;">
                        Assigned: {sheet.get('assigned_to', 'Unassigned')}
                    </div>
                    <div style="font-size: 12px; color: #888;">
                        Status: {sheet.get('status', 'Pending')}
                    </div>
                    <div style="margin-top: 4px; height: 4px; background: #eee; border-radius: 2px;">
                        <div style="
                            width: {sheet.get('counted_items', 0) / sheet.get('total_items', 1) * 100}%;
                            height: 4px;
                            background: {get_progress_color(sheet.get('counted_items', 0) / sheet.get('total_items', 1))};
                            border-radius: 2px;
                        "></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
    
    # Items
    st.markdown("#### 📦 Items in Count")
    
    items_data = []
    for item_name, details in count['items'].items():
        items_data.append({
            'Item': item_name,
            'System Qty': details['system_qty'],
            'Counted Qty': details['counted_qty'],
            'Variance': details['variance'],
            'Status': details['status'],
            'Unit': details['unit'],
            'Notes': details['notes']
        })
    
    df = pd.DataFrame(items_data)
    
    # Apply styling for variance
    def style_variance(val):
        if isinstance(val, (int, float)) and val != 0:
            return 'background-color: #f8d7da; color: #721c24; font-weight: bold;'
        return ''
    
    st.dataframe(
        df.style.applymap(style_variance, subset=['Variance']),
        use_container_width=True,
        hide_index=True
    )
    
    if st.button("← Back to Counts", key=f"back_view_{count_id}"):
        st.session_state.stock_take_action = None
        st.rerun()


def enter_counts_interface(count_id, supabase_client=None):
    """
    Interface for entering counts (inFlow style) -- one row per item with
    its own Save button. Rendered full-width now (see
    active_counts_interface's dispatch), which was the actual fix for
    "squeezed vertically", not any change to this layout itself.
    """
    if count_id not in st.session_state.stock_takes:
        st.error("Count not found")
        return
    
    count = st.session_state.stock_takes[count_id]
    
    st.markdown(f"""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 20px;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="
            font-size: 1.3rem;
            font-weight: 600;
            margin-bottom: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">
            📝 Enter Counts - {count['name']}
        </div>
        <div style="color: #888; font-size: 13px;">
            📍 {count['warehouse']} | Progress: {count['progress']['counted']}/{count['progress']['total']}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Progress
    pct = (count['progress']['counted'] / count['progress']['total'] * 100) if count['progress']['total'] > 0 else 0
    st.progress(pct / 100, text=f"{pct:.0f}% Complete")
    
    # Items to count
    st.markdown("#### Items to Count")
    
    # Get pending items first
    items = []
    for item_name, details in count['items'].items():
        items.append({
            'Item': item_name,
            'System Qty': details['system_qty'],
            'Unit': details['unit'],
            'Status': details['status'],
            'Counted': details['counted_qty'],
            'Variance': details['variance'],
            'Notes': details['notes']
        })
    
    # Sort pending first
    items.sort(key=lambda x: x['Status'] != 'Pending')
    
    # Display items with quick entry
    for item in items:
        with st.container():
            col1, col2, col3, col4 = st.columns([2, 1, 1, 2])
            
            with col1:
                st.markdown(f"**{item['Item']}**")
                st.caption(f"System: {item['System Qty']} {item['Unit']}")
            
            with col2:
                status_icon = "✅" if item['Status'] == 'Counted' else "⏳"
                st.caption(f"{status_icon} {item['Status']}")
            
            with col3:
                if item['Status'] == 'Counted':
                    st.caption(f"Counted: {item['Counted']}")
                    if item['Variance'] != 0:
                        st.caption(f"Variance: {item['Variance']:+.0f}")
            
            with col4:
                if item['Status'] != 'Counted':
                    counted_qty = st.number_input(
                        "Count",
                        min_value=0.0,
                        value=float(item['System Qty']),
                        step=1.0,
                        key=f"count_{count_id}_{item['Item']}",
                        label_visibility="collapsed"
                    )
                    
                    if st.button("✓ Save", key=f"save_{count_id}_{item['Item']}", type="primary"):
                        success, message = enter_count(
                            count_id, 
                            item['Item'], 
                            counted_qty,
                            notes=item['Notes'],
                            supabase_client=supabase_client,
                        )
                        if success:
                            st.success(message)
                        else:
                            st.error(message)
            
            st.markdown("---")
    
    # Complete button
    if count['progress']['counted'] >= count['progress']['total']:
        st.markdown("---")
        col1, col2, col3 = st.columns([1, 1, 1])
        with col2:
            if st.button("✅ Complete & Adjust", type="primary", use_container_width=True):
                success, message = complete_and_adjust(count_id, supabase_client=supabase_client)
                if success:
                    st.success(message)
                    st.balloons()
                else:
                    st.error(message)
    
    if st.button("← Back to Counts", key=f"back_enter_{count_id}"):
        st.session_state.stock_take_action = None
        st.rerun()
        

def assign_sheets_interface(count_id, supabase_client=None):
    """
    Interface for assigning sheets to team members (inFlow feature)
    """
    if count_id not in st.session_state.stock_takes:
        st.error("Count not found")
        return
    
    count = st.session_state.stock_takes[count_id]
    
    st.markdown(f"""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 12px;
        padding: 16px;
        margin: 10px 0;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="font-weight: 600;">👥 Assign Sheets - {count['name']}</div>
        <div style="font-size: 13px; color: #888;">
            Assign count sheets to team members for efficient counting.
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    if not count['sheets']:
        st.info("No sheets to assign. Create sheets first.")
        return
    
    team_members = _get_team_members(supabase_client)
    
    sheet_ids = [sid for sid in count['sheets'] if st.session_state.count_sheets.get(sid)]
    cols = st.columns(3)
    for idx, sheet_id in enumerate(sheet_ids):
        sheet = st.session_state.count_sheets.get(sheet_id, {})
        with cols[idx % 3]:
            st.markdown(f"**{sheet.get('name', 'Unknown')}**")
            st.caption(f"{sheet.get('counted_items', 0)}/{sheet.get('total_items', 0)} items")

            current_assign = sheet.get('assigned_to', 'Unassigned')
            selected_user = st.selectbox(
                "Assign to",
                team_members,
                index=team_members.index(current_assign) if current_assign in team_members else 0,
                key=f"assign_{sheet_id}",
                label_visibility="collapsed"
            )
            if selected_user != current_assign and selected_user != "Unassigned":
                if assign_sheet_to_user(sheet_id, selected_user, supabase_client=supabase_client):
                    st.success(f"✅ Assigned to {selected_user}")

            st.caption(f"Status: {sheet.get('status', 'Pending')}")
            st.markdown("---")
    
    if st.button("← Back", key=f"back_assign_{count_id}"):
        st.session_state.stock_take_action = None
        st.rerun()
        

def count_history_interface():
    """
    Interface for viewing count history    """
    st.markdown("""
    <div style="
        background: rgba(255,255,255,0.05);
        backdrop-filter: blur(10px);
        border-radius: 16px;
        padding: 20px;
        margin-bottom: 20px;
        border: 1px solid rgba(255,255,255,0.08);
    ">
        <div style="
            font-size: 1.3rem;
            font-weight: 600;
            margin-bottom: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        ">
            📜 Count History
        </div>
        <div style="color: #888; font-size: 13px;">
            View completed stock counts and their adjustments.
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    history = get_count_history()
    
    if not history:
        st.info("No completed counts found")
        return
    
    # Summary stats
    total_adjustments = sum(h['adjustments'] for h in history)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📋 Total Counts", len(history))
    with col2:
        st.metric("📊 Total Adjustments", total_adjustments)
    with col3:
        avg_adjustments = total_adjustments / len(history) if history else 0
        st.metric("📈 Avg Adjustments/Count", f"{avg_adjustments:.1f}")
    
    st.markdown("---")
    
    # Display history
    history_df = pd.DataFrame(history)
    st.dataframe(history_df, use_container_width=True, hide_index=True)
    
    # Export
    if not history_df.empty:
        csv = history_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download History CSV",
            data=csv,
            file_name=f"stock_take_history_{datetime.now().strftime('%Y%m%d')}.csv",
            mime='text/csv'
        )