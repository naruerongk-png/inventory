# pages.py
import streamlit as st
import pandas as pd
import plotly.express as px
import time
from datetime import datetime
from PIL import Image
from io import BytesIO
from streamlit_drawable_canvas import st_canvas

# Import from utils instead of inventory
from utils import (
    load_data,
    calculate_depreciation,
    sync_glpi_data,
    process_borrow,
    process_return,
    send_repair,
    finish_repair,
    audit_asset,
    update_asset,
    soft_delete,
    add_asset,
    generate_qr,
    create_bulk_qr_pdf,
    create_professional_pdf,
    create_handover_pdf,
    restore_asset,
    get_all_users,
    add_user,
    admin_change_user_password,
    delete_user,
    GlpiApi
)

def show_dashboard(df):
    if not df.empty:
        df['warranty_date'] = pd.to_datetime(df['warranty_date'], errors='coerce')
        df['purchase_date'] = pd.to_datetime(df['purchase_date'], errors='coerce')
        
        df['depreciated_value'] = df.apply(calculate_depreciation, axis=1)
        total_cost = df['price'].sum()
        total_current = df['depreciated_value'].sum()
        
        today = pd.Timestamp.now().normalize()
        exp = len(df[(df['warranty_date'] < today) & (df['warranty_date'].notna())])
        
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Assets", len(df))
        c2.metric("Total Purchase Value", f"{total_cost:,.0f} B")
        c3.metric("Total Current Value", f"{total_current:,.0f} B", delta=f"Lost {(total_cost-total_current):,.0f} B", delta_color="inverse")
        c4.metric("In Repair", len(df[df['status']=='Repair']))
        c5.metric("Warranty Expired", exp, delta_color="inverse")
        
        st.markdown("---")
        cl, cr = st.columns([2,1])
        with cl: 
            if 'purchase_date' in df.columns and not df['purchase_date'].isna().all():
                df_time = df.groupby(df['purchase_date'].dt.to_period("M").astype(str)).size().reset_index(name='count')
                fig_line = px.bar(df_time, x='purchase_date', y='count', title="Asset Purchase Trend")
                st.plotly_chart(fig_line, use_container_width=True)
        with cr: 
            st.plotly_chart(px.pie(df, names='status', title="Asset Status Distribution", hole=0.4), use_container_width=True)
        
        st.subheader("Category Value Analysis")
        cat_grp = df.groupby('category')[['price', 'depreciated_value']].sum().reset_index()
        cat_grp = pd.melt(cat_grp, id_vars=['category'], value_vars=['price', 'depreciated_value'], var_name='Type', value_name='Value')
        st.plotly_chart(px.bar(cat_grp, x='category', y='Value', color='Type', barmode='group', title="Purchase Value vs Current Value"), use_container_width=True)
    else:
        st.info("No data in the system yet.")

def show_glpi_sync():
    st.header("GLPI Data Sync")
    if 'glpi_df' not in st.session_state: st.session_state.glpi_df = None

    st.markdown("### GLPI Sync Configuration")
    glpi_creds = st.secrets.get("glpi", {})

    with st.form("glpi_form"):
        api_url = st.text_input("API URL", value=glpi_creds.get("api_url", ""))
        app_token = st.text_input("App Token", value=glpi_creds.get("app_token", ""), type="password")
        user_token = st.text_input("User Token", value=glpi_creds.get("user_token", ""), type="password")
        submitted = st.form_submit_button("Fetch GLPI Computers")

        if submitted:
            if not all([api_url, app_token, user_token]):
                st.error("API URL, App Token, and User Token are required.")
            else:
                with st.spinner("Connecting to GLPI..."):
                    glpi = GlpiApi(api_url=api_url, app_token=app_token, user_token=user_token)
                    computers, error = glpi.get_computers()
                    glpi.kill_session()

                    if error:
                        st.error(f"Failed to fetch data from GLPI: {error}")
                        st.session_state.glpi_df = None
                    elif computers is not None:
                        st.success(f"Successfully fetched {len(computers)} computers!")
                        df = pd.DataFrame(computers)
                        if 'users_id' in df.columns: df['users_id'] = df['users_id'].astype(str)
                        st.session_state.glpi_df = df
                    else:
                        st.info("No computers found.")
                        st.session_state.glpi_df = None
    
    if st.session_state.glpi_df is not None and not st.session_state.glpi_df.empty:
        st.markdown("---")
        st.subheader("Fetched GLPI Data Preview")
        st.dataframe(st.session_state.glpi_df.head())
        
        if st.button("Sync GLPI Data to Local DB", type="primary"):
            with st.spinner("Synchronizing data..."):
                inserts, updates, errors = sync_glpi_data(st.session_state.glpi_df)
                st.success(f"Sync complete! Inserted: {inserts}, Updated: {updates}, Errors: {errors}")
                st.rerun()

def show_borrow_return(df):
    st.header("Bulk Borrow & Return")
    cb, cr = st.columns(2)
    
    with cb:
        with st.container(border=True):
            st.subheader("Borrow Assets")
            stock = df[~df['status'].isin(['In Use', 'Retired', 'Repair', 'Lost'])]
            
            if not stock.empty:
                stock['display'] = stock['asset_tag'] + " | " + stock['model']
                selected_items = st.multiselect("Select items to borrow:", stock['display'].tolist())
                b_u = st.text_input("Borrower Name")
                b_n = st.text_input("Note", key="bn")
                
                st.write("Signature:")
                signature = st_canvas(
                    fill_color="rgba(255, 165, 0, 0.3)", stroke_width=2, stroke_color="#000000",
                    background_color="#eeeeee", height=150, width=400, drawing_mode="freedraw", key="canvas_borrow"
                )
                
                if st.button("Confirm Borrow"):
                    if b_u and selected_items:
                        sig_image = Image.fromarray(signature.image_data) if signature.image_data is not None else None
                        pdf_items = []
                        success_count = 0
                        
                        for item_str in selected_items:
                            tag = item_str.split(" | ")[0]
                            item_data = df[df['asset_tag'] == tag].iloc[0]
                            success, msg = process_borrow(tag, b_u, b_n, signature_blob=sig_image)
                            if success:
                                success_count += 1
                                pdf_items.append({
                                    'tag': tag, 'model': item_data['model'],
                                    'serial': item_data.get('serial_number', '-'), 'specs': item_data.get('specs', '-')
                                })
                            else: st.error(f"Error for {tag}: {msg}")
                        
                        if success_count > 0:
                            pdf_bytes = create_professional_pdf(pdf_items, b_u, b_n, signature_img=sig_image)
                            st.success(f"Borrowed {success_count} items successfully!")
                            st.download_button("Download Handover PDF", pdf_bytes, f"Handover_{b_u}.pdf", "application/pdf")
                            time.sleep(2); st.rerun()
                    else: st.warning("Please select items and enter borrower name.")
            else: st.info("No items available to borrow.")

    with cr:
        with st.container(border=True):
            st.subheader("Return Assets")
            in_use = df[df['status']=='In Use']
            
            if not in_use.empty:
                borrowers = in_use['assigned_to'].unique()
                selected_user = st.selectbox("Filter by User:", borrowers)
                user_assets = in_use[in_use['assigned_to'] == selected_user]
                
                if not user_assets.empty:
                    user_assets['display'] = user_assets['asset_tag'] + " | " + user_assets['model']
                    return_items = st.multiselect(f"Items held by {selected_user}:", user_assets['display'].tolist())
                    r_n = st.text_input("Return Note", key="rn")
                    
                    if st.button("Confirm Return"):
                        if return_items:
                            success_count = 0
                            for item_str in return_items:
                                tag = item_str.split(" | ")[0]
                                success, msg = process_return(tag, r_n)
                                if success: success_count += 1
                                else: st.error(f"Error for {tag}: {msg}")
                            
                            if success_count > 0:
                                st.success(f"Returned {success_count} items successfully!")
                                time.sleep(2); st.rerun()
                        else: st.warning("Please select items to return.")
                else: st.info("This user has no borrowed items.")
            else: st.info("No items are currently borrowed.")

def show_maintenance(df):
    st.header("🔧 Maintenance")
    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.subheader("Send for Repair")
            available_assets = df[df['status'] == 'In Stock']
            if not available_assets.empty:
                available_assets['display'] = available_assets['asset_tag'] + " | " + available_assets['model']
                asset_to_repair = st.selectbox("Select Asset", available_assets['display'].tolist(), index=None)
                
                with st.form("send_repair_form"):
                    vendor = st.text_input("Repair Vendor")
                    issue = st.text_area("Issue Description")
                    submitted = st.form_submit_button("Send for Repair")

                    if submitted and asset_to_repair:
                        asset_tag = asset_to_repair.split(" | ")[0]
                        success, message = send_repair(asset_tag, vendor, issue)
                        if success:
                            st.success("Asset sent for repair!")
                            st.rerun()
                        else: st.error(f"Error: {message}")
            else: st.info("No 'In Stock' assets available.")

    with col2:
        with st.container(border=True):
            st.subheader("Finish Repair")
            repair_assets = df[df['status'] == 'Repair']
            if not repair_assets.empty:
                repair_assets['display'] = repair_assets['asset_tag'] + " | " + repair_assets['model']
                asset_to_finish = st.selectbox("Select Asset", repair_assets['display'].tolist(), index=None)

                with st.form("finish_repair_form"):
                    cost = st.number_input("Repair Cost", min_value=0.0, step=50.0)
                    note = st.text_input("Repair Notes / Summary")
                    submitted = st.form_submit_button("Mark as Repaired")

                    if submitted and asset_to_finish:
                        asset_tag = asset_to_finish.split(" | ")[0]
                        success, message = finish_repair(asset_tag, cost, note)
                        if success:
                            st.success("Repair finished and asset is now 'In Stock'.")
                            st.rerun()
                        else: st.error(f"Error: {message}")
            else: st.info("No assets currently under repair.")

    st.markdown("---")
    st.subheader("Maintenance Log")
    maintenance_df = load_data("maintenance_logs")
    if not maintenance_df.empty:
        st.dataframe(maintenance_df, use_container_width=True, hide_index=True)
    else: st.info("No maintenance records found.")

def show_audit(df):
    st.header("Asset Audit")
    if not df.empty:
        df['display'] = df['asset_tag'] + " | " + df['model']
        selected_asset = st.selectbox("Select Asset to Audit", df['display'].tolist())
        if st.button("Mark Audited"):
            tag = selected_asset.split(" | ")[0]
            audit_asset(tag)
            st.success(f"Audit timestamp updated for {tag}")
            st.rerun()
    else: st.info("No assets to audit.")

def show_search(df):
    st.header("🔍 Search Assets")
    if df.empty:
        st.info("No assets to search.")
        return

    search_query = st.text_input("Search by Tag, Model, Serial, or Assigned User", "")
    col1, col2 = st.columns(2)
    with col1: status_filter = st.multiselect("Filter by Status", df['status'].unique(), default=df['status'].unique())
    with col2: category_filter = st.multiselect("Filter by Category", df['category'].unique(), default=df['category'].unique())

    filtered_df = df[df['status'].isin(status_filter) & df['category'].isin(category_filter)]

    if search_query:
        query = search_query.lower()
        search_cols = ['asset_tag', 'model', 'serial_number', 'assigned_to']
        filtered_df = filtered_df[
            filtered_df[search_cols].fillna('').astype(str).apply(
                lambda x: x.str.lower().str.contains(query)
            ).any(axis=1)
        ]
    st.dataframe(filtered_df, use_container_width=True, hide_index=True)

def show_manage(df):
    st.header("🛠️ Manage Assets")
    if df.empty:
        st.info("No assets to manage.")
        return

    df['display'] = df['asset_tag'] + " | " + df['model'] + " | " + df['assigned_to'].fillna('')
    asset_list = df['display'].tolist()
    selected_asset_str = st.selectbox("Select Asset to Manage", asset_list, index=None, placeholder="Search for an asset...")

    if selected_asset_str:
        asset_tag = selected_asset_str.split(" | ")[0]
        asset_data = df[df['asset_tag'] == asset_tag].iloc[0].to_dict()

        with st.form("manage_asset_form"):
            st.subheader(f"Editing: {asset_data['asset_tag']}")
            c1, c2 = st.columns(2)
            with c1:
                model = st.text_input("Model", value=asset_data.get('model'))
                category = st.selectbox("Category", ["Laptop", "Desktop", "Monitor", "Printer", "Network Gear", "Other"], index=["Laptop", "Desktop", "Monitor", "Printer", "Network Gear", "Other"].index(asset_data.get('category', 'Other')))
                serial_number = st.text_input("Serial Number", value=asset_data.get('serial_number'))
                status = st.selectbox("Status", ["In Stock", "In Use", "Repair", "Retired"], index=["In Stock", "In Use", "Repair", "Retired"].index(asset_data.get('status', 'In Stock')))

            with c2:
                try: p_date_val = datetime.strptime(str(asset_data.get('purchase_date')).split(" ")[0], '%Y-%m-%d').date() if asset_data.get('purchase_date') else None
                except: p_date_val = None
                purchase_date = st.date_input("Purchase Date", value=p_date_val)
                price = st.number_input("Price", min_value=0.0, step=100.0, value=float(asset_data.get('price', 0.0)))
                try: w_date_val = datetime.strptime(str(asset_data.get('warranty_date')).split(" ")[0], '%Y-%m-%d').date() if asset_data.get('warranty_date') else None
                except: w_date_val = None
                warranty_date = st.date_input("Warranty Expiry Date", value=w_date_val)
                vendor = st.text_input("Vendor/Supplier", value=asset_data.get('vendor'))

            assigned_to = st.text_input("Assigned To", value=asset_data.get('assigned_to'))
            department = st.text_input("Department / Location", value=asset_data.get('department'))
            specs = st.text_area("Specifications", value=asset_data.get('specs'))
            
            update_button = st.form_submit_button("💾 Update Asset", type="primary")
            delete_button = st.form_submit_button("🗑️ Delete Asset (to Bin)")

            if update_button:
                p_date_str = str(purchase_date) if purchase_date else None
                w_date_str = str(warranty_date) if warranty_date else None
                success, message = update_asset(asset_tag, category, model, serial_number, status, assigned_to,
                    p_date_str, price, w_date_str, vendor, department, specs)
                if success:
                    st.success("Asset updated successfully!")
                    st.rerun()
                else: st.error(f"Failed to update asset: {message}")

            if delete_button:
                soft_delete(asset_tag)
                st.warning(f"Asset {asset_tag} moved to Recycle Bin.")
                st.rerun()

def show_add_asset():
    st.header("➕ Add New Asset")
    with st.form("add_asset_form", clear_on_submit=True):
        st.subheader("Asset Details")
        c1, c2 = st.columns(2)
        with c1:
            asset_tag = st.text_input("Asset Tag (Unique ID)")
            model = st.text_input("Model")
            category = st.selectbox("Category", ["Laptop", "Desktop", "Monitor", "Printer", "Network Gear", "Other"])
            serial_number = st.text_input("Serial Number")
            status = st.selectbox("Status", ["In Stock", "In Use", "Repair", "Retired"])
        with c2:
            purchase_date = st.date_input("Purchase Date", value=None)
            price = st.number_input("Price", min_value=0.0, step=100.0)
            warranty_date = st.date_input("Warranty Expiry Date", value=None)
            vendor = st.text_input("Vendor/Supplier")
            assigned_to = st.text_input("Assigned To (if In Use)")
        department = st.text_input("Department / Location")
        specs = st.text_area("Specifications")
        image_blob = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg"])
        
        submitted = st.form_submit_button("Add Asset", type="primary")
        if submitted:
            p_date_str = str(purchase_date) if purchase_date else None
            w_date_str = str(warranty_date) if warranty_date else None
            success, message = add_asset(asset_tag, category, model, serial_number, status, assigned_to,
                p_date_str, price, w_date_str, vendor, department, image_blob, specs)
            if success:
                st.success("Asset added successfully!")
                st.rerun()
            else: st.error(f"Failed to add asset: {message}")

def show_qr_code(df):
    st.header("QR Code Generator")
    if not df.empty:
        df['display'] = df['asset_tag'] + " | " + df['model']
        selected_assets = st.multiselect("Select Assets for QR Code", df['display'].tolist())
        if st.button("Generate QR Codes"):
            if selected_assets:
                data_list = []
                for item in selected_assets:
                    tag = item.split(" | ")[0]
                    row = df[df['asset_tag'] == tag].iloc[0]
                    data_list.append({'tag': tag, 'model': row['model'], 'dept': row.get('department', 'Common')})
                
                pdf_bytes = create_bulk_qr_pdf(data_list)
                st.download_button("Download QR Codes (PDF)", pdf_bytes, "qr_codes.pdf", "application/pdf")
            else: st.warning("Please select at least one asset.")
    else: st.info("No assets available.")

def show_logs_reprint():
    st.header("Logs & Document Reprint")
    st.subheader("Recent Borrowing Logs")
    logs = load_data("borrow_logs")
    st.dataframe(logs, use_container_width=True, hide_index=True)

def show_bin():
    st.header("Recycle Bin")
    bin_df = load_data("recycle_bin")
    if not bin_df.empty:
        st.dataframe(bin_df)
        bin_df['display'] = bin_df['asset_tag'] + " | " + bin_df['model']
        selected = st.selectbox("Select Asset to Restore", bin_df['display'].tolist())
        if st.button("Restore Asset"):
            tag = selected.split(" | ")[0]
            res, msg = restore_asset(tag)
            if res:
                st.success("Asset Restored!")
                st.rerun()
            else: st.error(msg)
    else: st.info("Recycle Bin is empty.")

def show_admin_page():
    st.header("👨‍💼 Admin Tools")
    st.subheader("User Management")
    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.subheader("Add New User")
            with st.form("add_user_form", clear_on_submit=True):
                new_username = st.text_input("New Username")
                new_password = st.text_input("New Password", type="password")
                submitted = st.form_submit_button("Add User")
                if submitted:
                    success, message = add_user(new_username, new_password)
                    if success: st.success(message); st.rerun()
                    else: st.error(message)
        
        with st.container(border=True):
            st.subheader("Change User Password")
            users_df = get_all_users()
            if not users_df.empty:
                user_list = users_df['username'].tolist()
                selected_user = st.selectbox("Select User", user_list)
                with st.form("admin_change_password_form", clear_on_submit=True):
                    new_password = st.text_input("Enter New Password", type="password")
                    submitted = st.form_submit_button("Change Password")
                    if submitted:
                        success, message = admin_change_user_password(selected_user, new_password)
                        if success: st.success(message)
                        else: st.error(message)

    with col2:
        with st.container(border=True):
            st.subheader("Existing Users")
            users_df = get_all_users()
            if not users_df.empty:
                st.dataframe(users_df, use_container_width=True, hide_index=True)
                user_to_delete = st.selectbox("Select User to Delete", users_df['username'].tolist(), index=None, placeholder="Select user...")
                if user_to_delete and st.button(f"Delete {user_to_delete}", type="primary"):
                    success, message = delete_user(user_to_delete)
                    if success: st.success(message); st.rerun()
                    else: st.error(message)
            else: st.info("No users found.")