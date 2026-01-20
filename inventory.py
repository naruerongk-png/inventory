# inventory.py
import streamlit as st
import extra_streamlit_components as stx
import time
import os
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta

# Import Logic from utils
from utils import (
    check_login, user_exists, change_password, load_data, add_asset
)

# --- CONFIGURATION ---
os.environ['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] = 'false'
st.set_page_config(page_title="IT Asset Master V16", layout="wide", page_icon="🏢")

# Global Cookie Manager
cookie_manager = stx.CookieManager()

# Import Pages (Import after utils to avoid circular dependency)
from pages import (
    show_dashboard, show_glpi_sync, show_borrow_return, show_maintenance,
    show_audit, show_search, show_manage, show_add_asset, show_qr_code,
    show_logs_reprint, show_bin, show_admin_page
)

def login_page():
    c1, c2, c3 = st.columns([3, 1, 3]) 
    with c2:
        if os.path.exists("LOGO ARI.png"):
            st.image("LOGO ARI.png", width=250)
        else:
            st.warning("Logo not found")

    st.markdown("<h1 style='text-align: center;'>🔐 เข้าสู่ระบบ (IT Asset System)</h1>", unsafe_allow_html=True)
    
    cookie_user = cookie_manager.get(cookie="asset_auth_token")
    
    if cookie_user and user_exists(cookie_user):
        st.session_state['logged_in'] = True
        st.session_state['username'] = cookie_user
        st.success(f"ยินดีต้อนรับกลับมา! {cookie_user}")
        time.sleep(0.5)
        st.rerun()
        return

    c1, c2, c3 = st.columns([2, 2, 2])
    with c2:
        with st.form("login_form"):
            user = st.text_input("ชื่อผู้ใช้ (Username)")
            pwd = st.text_input("รหัสผ่าน (Password)", type="password")
            submitted = st.form_submit_button("เข้าสู่ระบบ", width='stretch')
            
            if submitted:
                if check_login(user, pwd):
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = user
                    expires = datetime.now() + timedelta(days=7)
                    cookie_manager.set("asset_auth_token", user, expires_at=expires)
                    st.success("ยินดีต้อนรับ!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

def main_app():
    if os.path.exists("LOGO ARI.png"):
        st.sidebar.image("LOGO ARI.png", width=250)
    st.sidebar.markdown("---")

    st.sidebar.title(f"👤 {st.session_state['username']}")
    st.sidebar.caption("IT Asset Master V16")

    with st.sidebar.expander("🔑 Change Password"):
        with st.form("change_password_form", clear_on_submit=True):
            current_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            submitted = st.form_submit_button("Change Password")
            
            if submitted:
                if not all([current_password, new_password, confirm_password]):
                    st.warning("Please fill all fields.")
                elif new_password != confirm_password:
                    st.error("New passwords do not match.")
                else:
                    if change_password(st.session_state['username'], current_password, new_password):
                        st.success("Password changed successfully! Please log in again.")
                        time.sleep(2)
                        try: cookie_manager.delete("asset_auth_token")
                        except KeyError: pass
                        st.session_state['logged_in'] = False
                        st.session_state['username'] = None
                        st.rerun()
                    else: st.error("Incorrect current password.")
    
    if st.sidebar.button("Logout", type="primary"):
        try: cookie_manager.delete("asset_auth_token")
        except KeyError: pass
        st.session_state['logged_in'] = False
        st.session_state['username'] = None
        time.sleep(1) 
        st.rerun()
    
    st.sidebar.markdown("---")
    st.sidebar.header("File Management")
    
    df_export = load_data("assets")
    if not df_export.empty:
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Assets')
        
        st.sidebar.download_button(
            label="Export to Excel",
            data=output.getvalue(),
            file_name=f"Asset_Export_{datetime.now().date()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    
    if os.path.exists("it_inventory.db"):
        with open("it_inventory.db", "rb") as fp:
            st.sidebar.download_button("Backup Database", fp, "backup.db")

    st.sidebar.markdown("---")
    up_file = st.sidebar.file_uploader("Import from Excel", type=['xlsx'])
    if up_file and st.sidebar.button("Start Import"):
        try:
            df_i = pd.read_excel(up_file)
            count = 0
            for _, r in df_i.iterrows():
                # Simplified import logic
                add_asset(str(r['Asset Tag']), str(r['Category']), str(r['Model']), str(r['Serial']), str(r['Status']), str(r['Assigned To']), str(r['Date']).split(" ")[0], float(r.get('Price', 0.0)), None, None, "Common", None, str(r.get('Specs', '')))
                count += 1
            st.sidebar.success(f"Imported {count} items.")
            time.sleep(1); st.rerun()
        except Exception as e:
            st.sidebar.error(f"Error: {e}")

    df = load_data("assets")
    st.sidebar.markdown("---")
    st.sidebar.header("Main Menu")

    menu_list = [
        "📈 Dashboard", "💻 GLPI Sync", "🔁 Borrow/Return", "🔧 Maintenance", 
        "✅ Audit", "🔍 Search", "🛠️ Manage", "➕ Add", 
        "🖨️ QR Code", "📋 Logs & Reprint", "🗑️ Bin"
    ]
    if st.session_state.get('username') == 'admin':
        menu_list.append("👨‍💼 Admin")

    page = st.sidebar.radio("Navigate", menu_list)

    if page == "📈 Dashboard": show_dashboard(df)
    elif page == "💻 GLPI Sync": show_glpi_sync()
    elif page == "🔁 Borrow/Return": show_borrow_return(df)
    elif page == "🔧 Maintenance": show_maintenance(df)
    elif page == "✅ Audit": show_audit(df)
    elif page == "🔍 Search": show_search(df)
    elif page == "🛠️ Manage": show_manage(df)
    elif page == "➕ Add": show_add_asset()
    elif page == "🖨️ QR Code": show_qr_code(df)
    elif page == "📋 Logs & Reprint": show_logs_reprint()
    elif page == "🗑️ Bin": show_bin()
    elif page == "👨‍💼 Admin": show_admin_page()

# --- APP FLOW ---
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

cookie_user = cookie_manager.get(cookie="asset_auth_token")
if not st.session_state['logged_in'] and cookie_user and user_exists(cookie_user):
    st.session_state['logged_in'] = True
    st.session_state['username'] = cookie_user

if st.session_state['logged_in']:
    main_app()
else:
    login_page()