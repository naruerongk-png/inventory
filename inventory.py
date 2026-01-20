import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import time
import os
import numpy as np
from PIL import Image
import qrcode
from io import BytesIO
from datetime import datetime, timedelta
from fpdf import FPDF
from streamlit_drawable_canvas import st_canvas
import extra_streamlit_components as stx
import hashlib
import logging
from glpi_client import GlpiApi

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 1. CONFIGURATION ---
# Disable telemetry to prevent errors
import os
os.environ['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] = 'false'

st.set_page_config(page_title="IT Asset Master V16", layout="wide", page_icon="🏢")

# Global Cookie Manager
cookie_manager = stx.CookieManager()

# --- 2. AUTHENTICATION ---

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def check_login(username, password):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT password FROM users WHERE username=?", (username,))
        result = cursor.fetchone()
        
        if result:
            stored_password = result[0]
            # Check if stored password is hashed (64 chars for SHA256) or plain text (backward compatibility)
            if stored_password and len(stored_password) == 64:
                # Compare hashed passwords
                hashed_input = hash_password(password)
                if stored_password == hashed_input:
                    return True
            else:
                # Legacy plain text password - migrate to hashed
                if stored_password == password:
                    # Update to hashed password
                    hashed = hash_password(password)
                    cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed, username))
                    conn.commit()
                    logger.info(f"Migrated password for user: {username}")
                    return True
        logger.warning(f"Login failed for user: {username}")
        return False
    except Exception as e:
        logger.error(f"Login error for {username}: {e}")
        return False
    finally:
        conn.close()

def change_password(username, old_password, new_password):
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Get current password
        cursor.execute("SELECT password FROM users WHERE username=?", (username,))
        result = cursor.fetchone()
        if not result:
            logger.warning(f"User not found: {username}")
            return False

        stored_password = result[0]
        # Check password (support both hashed and plain text for migration)
        password_match = False
        if len(stored_password) == 64:
            password_match = stored_password == hash_password(old_password)
        else:
            password_match = stored_password == old_password
        
        if password_match:
            # Update with hashed password
            hashed_new = hash_password(new_password)
            cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed_new, username))
            conn.commit()
            logger.info(f"Password changed for user: {username}")
            return True
        else:
            logger.warning(f"Wrong password attempt for user: {username}")
            return False
    except Exception as e:
        logger.error(f"Error changing password for {username}: {e}")
        return False
    finally:
        conn.close()

def user_exists(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE username=?", (username,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def get_all_users():
    """Get all users from database"""
    conn = get_connection()
    try:
        df = pd.read_sql_query("SELECT id, username FROM users ORDER BY username", conn)
        return df
    except Exception as e:
        logger.error(f"Error loading users: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def add_user(username, password):
    """Add a new user"""
    if not username or not username.strip():
        return False, "Username cannot be empty"
    if len(username) < 3:
        return False, "Username must be at least 3 characters"
    if user_exists(username):
        return False, "Username already exists"
    if not password or len(password) < 3:
        return False, "Password must be at least 3 characters"
    
    conn = get_connection()
    try:
        hashed = hash_password(password)
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed))
        conn.commit()
        logger.info(f"User added: {username}")
        return True, "User added successfully"
    except Exception as e:
        logger.error(f"Error adding user {username}: {e}")
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def delete_user(username):
    """Delete a user"""
    if username == 'admin':
        return False, "Cannot delete admin user"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE username=?", (username,))
        if not cursor.fetchone():
            return False, "User not found"
        
        cursor.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()
        logger.info(f"User deleted: {username}")
        return True, "User deleted successfully"
    except Exception as e:
        logger.error(f"Error deleting user {username}: {e}")
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def admin_change_user_password(username, new_password):
    """Admin can change any user's password"""
    if not username:
        return False, "Username cannot be empty"
    if not new_password or len(new_password) < 3:
        return False, "Password must be at least 3 characters"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE username=?", (username,))
        if not cursor.fetchone():
            return False, "User not found"
        
        hashed = hash_password(new_password)
        cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed, username))
        conn.commit()
        logger.info(f"Password changed for user {username} by admin")
        return True, "Password changed successfully"
    except Exception as e:
        logger.error(f"Error changing password for {username}: {e}")
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally:
        conn.close()


def migrate_all_passwords_to_hashed():
    """Migrate all plain text passwords to hashed format"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username, password FROM users")
        users = cursor.fetchall()
        migrated_count = 0
        
        for username, stored_password in users:
            # Check if password is already hashed (64 chars for SHA256)
            if len(stored_password) != 64:
                # This is plain text, we need to hash it
                # But we don't know the original password, so we can't hash it
                # Instead, we'll set default passwords for known users
                default_passwords = {
                    "admin": "admin",
                    "user": "user",
                    "it": "password"
                }
                
                if username in default_passwords:
                    # Use default password for known users
                    hashed = hash_password(default_passwords[username])
                    cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed, username))
                    migrated_count += 1
                    logger.info(f"Migrated password for user: {username}")
        
        conn.commit()
        if migrated_count > 0:
            logger.info(f"Migrated {migrated_count} passwords to hashed format")
        return migrated_count
    except Exception as e:
        logger.error(f"Error migrating passwords: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()

def login_page():
    # จัด Layout: แบ่ง 3 ช่อง [ซ้าย, กลาง, ขวา]
    # ปรับเป็น [1, 1, 1] หรือ [3, 2, 3] เพื่อบีบช่องกลางให้แคบลงเล็กน้อยก็ได้
    c1, c2, c3 = st.columns([3, 1, 3]) 
    
    with c2:
        # 1. จัดให้รูปอยู่กึ่งกลางคอลัมน์ (Optional: ใส่ CSS เพื่อความชัวร์ หรือใช้แค่นี้ก็ได้)
        # 2. กำหนด width เป็น pixel (แนะนำ 200 - 300px สำหรับโลโก้)
        # Streamlit จะย่อรูปให้อัตโนมัติถ้าเปิดในมือถือที่จอเล็กกว่าค่า width ที่ตั้งไว้
        st.image("LOGO ARI.png", width=250) 

    st.markdown("<h1 style='text-align: center;'>🔐 เข้าสู่ระบบ (IT Asset System)</h1>", unsafe_allow_html=True)
    
    # Check for existing cookie
    cookie_user = cookie_manager.get(cookie="asset_auth_token")
    
    # Auto Login Logic
    if cookie_user and user_exists(cookie_user):
        st.session_state['logged_in'] = True
        st.session_state['username'] = cookie_user
        st.success(f"ยินดีต้อนรับกลับมา! {cookie_user}")
        time.sleep(0.5)
        st.rerun()
        return

    # Login Form
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
                    
                    # Set Cookie for 7 days
                    expires = datetime.now() + timedelta(days=7)
                    cookie_manager.set("asset_auth_token", user, expires_at=expires)
                    
                    st.success("ยินดีต้อนรับ!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

# --- 3. DATABASE & MIGRATION ---
def get_connection():
    return sqlite3.connect("it_inventory.db")

def init_and_migrate_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # --- Create Assets Table ---
    cursor.execute('''CREATE TABLE IF NOT EXISTS assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, asset_tag TEXT UNIQUE, category TEXT, model TEXT, 
        serial_number TEXT, status TEXT, assigned_to TEXT, purchase_date TEXT, 
        price REAL DEFAULT 0, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # --- Create Users Table ---
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )''')

    # --- Create Borrow Logs Table ---
    cursor.execute('''CREATE TABLE IF NOT EXISTS borrow_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_tag TEXT,
        borrower_name TEXT,
        action TEXT,
        note TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        signature_img BLOB
    )''')
    
    # --- Create Maintenance Logs Table ---
    cursor.execute('''CREATE TABLE IF NOT EXISTS maintenance_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_tag TEXT,
        vendor TEXT,
        issue TEXT,
        date_sent TEXT,
        date_received TEXT,
        cost REAL,
        status TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # --- Create History Table ---
    cursor.execute('''CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_tag TEXT,
        action TEXT,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # --- Create Recycle Bin Table ---
    cursor.execute('''CREATE TABLE IF NOT EXISTS recycle_bin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_tag TEXT,
        category TEXT,
        model TEXT,
        serial_number TEXT,
        status TEXT,
        assigned_to TEXT,
        purchase_date TEXT,
        price REAL,
        deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # --- Migrate Initial Users ---
    # Check if users table is empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        initial_users = {
            "admin": "admin",
            "user": "user",
            "it": "password"
        }
        for username, password in initial_users.items():
            hashed_password = hash_password(password)
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
    
    # --- Schema Migrations for assets table ---
    try: cursor.execute("ALTER TABLE assets ADD COLUMN warranty_date TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE assets ADD COLUMN vendor TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE assets ADD COLUMN last_audit_date TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE assets ADD COLUMN department TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE assets ADD COLUMN image_blob BLOB")
    except: pass
    try: cursor.execute("ALTER TABLE borrow_logs ADD COLUMN signature_img BLOB")
    except: pass
    try: cursor.execute("ALTER TABLE assets ADD COLUMN specs TEXT") 
    except: pass
    try: cursor.execute("ALTER TABLE assets ADD COLUMN location TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE assets ADD COLUMN comment TEXT")
    except: pass
    
    conn.commit(); conn.close()


init_and_migrate_db()

# Migrate existing plain text passwords to hashed format on startup
try:
    migrate_all_passwords_to_hashed()
except Exception as e:
    logger.warning(f"Password migration skipped: {e}")

# --- 4. HELPER FUNCTIONS ---

def validate_asset_tag(tag):
    """Validate asset tag format"""
    if not tag or not tag.strip():
        return False, "Asset Tag cannot be empty"
    if len(tag) > 50:
        return False, "Asset Tag is too long (max 50 characters)"
    return True, ""

def validate_price(price):
    """Validate price value"""
    try:
        price_float = float(price)
        if price_float < 0:
            return False, "Price cannot be negative"
        return True, ""
    except (ValueError, TypeError):
        return False, "Price must be a valid number"

def validate_date(date_str):
    """Validate date format"""
    if not date_str:
        return True, ""  # Optional field
    try:
        datetime.strptime(str(date_str), '%Y-%m-%d')
        return True, ""
    except (ValueError, TypeError):
        return False, "Invalid date format (expected YYYY-MM-DD)"

def load_data(table="assets"):
    conn = get_connection()
    try:
        if table == "maintenance_logs": 
            query = "SELECT * FROM maintenance_logs ORDER BY timestamp DESC"
        elif table == "recycle_bin": 
            query = "SELECT * FROM recycle_bin ORDER BY deleted_at DESC"
        elif table == "borrow_logs": 
            query = "SELECT * FROM borrow_logs ORDER BY timestamp DESC"
        elif table == "history": 
            query = "SELECT * FROM history ORDER BY timestamp DESC"
        else: 
            query = "SELECT * FROM assets"
        
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        logger.error(f"Error loading data from {table}: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def log_action(tag, action, detail):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO history (asset_tag, action, details) VALUES (?,?,?)", (tag, action, detail))
        conn.commit()
    except Exception as e:
        logger.error(f"Error logging action for {tag}: {e}")
        conn.rollback()
    finally:
        conn.close()

def generate_qr(data):
    qr = qrcode.QRCode(box_size=10, border=4); qr.add_data(data); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO(); img.save(buf); return buf.getvalue()

def calculate_depreciation(row):
    try:
        if not row['purchase_date'] or pd.isna(row['purchase_date']):
            return row['price']
        p_date = pd.to_datetime(row['purchase_date'])
        now = pd.Timestamp.now()
        age_years = (now - p_date).days / 365.25
        lifespan = 5
        depreciation_per_year = row['price'] / lifespan
        current_value = row['price'] - (depreciation_per_year * age_years)
        return max(0, current_value)
    except:
        return row['price']

class PDF(FPDF):
    def header(self):
        # Load fonts - Ensure these .ttf files are in your project directory
        # If files are missing, comment these lines out and use standard fonts
        try:
            self.add_font('Thai', '', 'THSarabunNew.ttf', uni=True)
            self.add_font('Thai', 'B', 'THSarabunNew Bold.ttf', uni=True) 
            self.add_font('Thai', 'I', 'THSarabunNew Italic.ttf', uni=True)
            self.add_font('Thai', 'BI', 'THSarabunNew BoldItalic.ttf', uni=True)
            self.set_font('Thai', 'B', 20)
        except RuntimeError:
             self.set_font('Arial', 'B', 15)

        self.cell(0, 10, 'IT Asset Handover Form', 0, 1, 'C')
        self.ln(5)

def create_handover_pdf(tag, model, user, note, signature_img=None):
    # For reprinting, we probably don't have serial and specs readily available
    # unless we query for them. Let's assume we only have the basics.
    items_list = [{
        'tag': tag,
        'model': model,
        'serial': '-', 
        'specs': ''
    }]
    return create_professional_pdf(items_list, user, note, signature_img)

# ฟังก์ชันสร้าง PDF แบบมืออาชีพ (Professional Layout)
def create_professional_pdf(items_list, user, note, signature_img=None):
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # --- 1. HEADER & LOGO ---
    # ใส่โลโก้ (ถ้ามีไฟล์)
    if os.path.exists("LOGO ARI.png"):
        # ปรับขนาดและตำแหน่งโลโก้ (x=10, y=8, w=30)
        pdf.image("LOGO ARI.png", 10, 8, 30)
    
    # ชื่อบริษัท/หัวข้อ
    try: pdf.set_font("Thai", 'B', 20)
    except: pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "IT Asset Handover Form (ใบส่งมอบอุปกรณ์ไอที)", 0, 1, 'C')
    
    try: pdf.set_font("Thai", size=12)
    except: pdf.set_font("Arial", size=10)
    pdf.cell(0, 5, "Official Document / เอกสารสำคัญ", 0, 1, 'C')
    pdf.ln(10) # เว้นบรรทัด

    # --- 2. ข้อมูลทั่วไป (General Info) ---
    pdf.set_fill_color(240, 240, 240) # สีเทาอ่อน
    pdf.set_draw_color(200, 200, 200) # เส้นขอบสีเทา
    
    # Date & Reference
    pdf.cell(130, 8, f" Borrower Name (ชื่อผู้ยืม):  {user}", 1, 0, 'L', fill=True)
    pdf.cell(60, 8, f" Date (วันที่): {datetime.now().strftime('%Y-%m-%d')}", 1, 1, 'L', fill=True)
    
    pdf.cell(190, 8, f" Note (หมายเหตุ):  {note}", 1, 1, 'L', fill=False)
    pdf.ln(5)

    # --- 3. ตารางรายการอุปกรณ์ (Assets Table) ---
    # Header Table
    pdf.set_fill_color(50, 50, 50) # หัวตารางสีเข้ม
    pdf.set_text_color(255, 255, 255) # ตัวหนังสือขาว
    try: pdf.set_font("Thai", 'B', 12)
    except: pdf.set_font("Arial", 'B', 10)
    
    # กำหนดความกว้างคอลัมน์ [No., Tag, Model, Serial/Specs]
    w = [15, 35, 50, 90] 
    
    pdf.cell(w[0], 8, "No.", 1, 0, 'C', fill=True)
    pdf.cell(w[1], 8, "Asset Tag", 1, 0, 'C', fill=True)
    pdf.cell(w[2], 8, "Model", 1, 0, 'C', fill=True)
    pdf.cell(w[3], 8, "Serial / Specs (รายละเอียด)", 1, 1, 'C', fill=True)
    
    # Body Table
    pdf.set_text_color(0, 0, 0) # กลับมาดำ
    try: pdf.set_font("Thai", size=11)
    except: pdf.set_font("Arial", size=10)
    
    for idx, item in enumerate(items_list):
        # เตรียมข้อมูล (Clean Text)
        tag = str(item.get('tag', '-'))
        model = str(item.get('model', '-'))
        serial = str(item.get('serial', '-'))
        specs = str(item.get('specs', ''))
        
        # รวม Serial และ Specs ไว้ในช่องเดียวกันเพื่อความประหยัดที่
        details = f"SN: {serial}"
        if specs and specs != 'None':
            details += f" | {specs}"
            
        # Clean Unicode for PDF compatibility (ถ้าใช้ฟอนต์ไทยไม่ต้อง encode latin-1 ก็ได้ แต่กันเหนียวไว้)
        def clean(t): return t # ถ้าลงฟอนต์ไทยแล้วไม่ต้อง encode
        
        pdf.cell(w[0], 8, str(idx+1), 1, 0, 'C')
        pdf.cell(w[1], 8, clean(tag), 1, 0, 'L')
        pdf.cell(w[2], 8, clean(model), 1, 0, 'L')
        
        # ใช้ MultiCell หรือ Cell ธรรมดา (ใช้ Cell ตัดคำถ้ายาวเกิน)
        # ตัดคำถ้ายาวเกิน 50 ตัวอักษร
        if len(details) > 60: details = details[:57] + "..."
        pdf.cell(w[3], 8, clean(details), 1, 1, 'L')

    pdf.ln(10)

    # --- 4. เงื่อนไขและลายเซ็น (Signature) ---
    try: pdf.set_font("Thai", size=10)
    except: pass
    pdf.multi_cell(0, 5, "Condition: The borrower acknowledges receipt of the above item(s) in good working condition and agrees to return them upon request. (ข้าพเจ้าได้รับอุปกรณ์สภาพสมบูรณ์และจะส่งคืนเมื่อสิ้นสุดการใช้งาน)")
    pdf.ln(10)

    # Signature Box
    y_sig = pdf.get_y()
    
    pdf.cell(95, 40, "", 1, 0) # กรอบซ้าย
    pdf.cell(95, 40, "", 1, 1) # กรอบขวา
    
    # แปะรูปลงในกรอบซ้าย (ถ้ามี)
    if signature_img is not None:
        temp_path = "temp_signature.png"
        signature_img.save(temp_path)
        # ปรับตำแหน่งให้เข้ากลางกรอบซ้าย
        pdf.image(temp_path, x=35, y=y_sig+5, w=40) 
        os.remove(temp_path)

    # Text ใต้กรอบ
    pdf.set_xy(10, y_sig + 32)
    pdf.cell(95, 5, f"Signed by: {user} (Borrower)", 0, 0, 'C')
    pdf.cell(95, 5, "Approved by: IT Support", 0, 1, 'C')
    
    return pdf.output(dest='S').encode('latin-1')

def create_bulk_qr_pdf(data_list):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    
    col_width = 45
    row_height = 50
    x_start = 10
    y_start = 10
    
    x = x_start
    y = y_start
    
    for i, item in enumerate(data_list):
        qr_img = qrcode.make(f"{item['tag']}\n{item['model']}")
        temp_path = f"temp_qr_{i}.png"
        qr_img.save(temp_path)
        
        if y + row_height > 280:
            pdf.add_page()
            x = x_start
            y = y_start
            
        pdf.rect(x, y, col_width, row_height)
        pdf.image(temp_path, x=x+2, y=y+2, w=40, h=40)
        pdf.set_xy(x, y+42)
        
        # Clean text for QR label to avoid unicode errors with standard FPDF
        tag_lbl = str(item['tag']).encode('latin-1', 'ignore').decode('latin-1')
        dept_lbl = str(item['dept']).encode('latin-1', 'ignore').decode('latin-1')
        
        pdf.multi_cell(col_width, 4, f"{tag_lbl}\n{dept_lbl}", 0, 'C')
        
        x += col_width + 2
        if x + col_width > 200:
            x = x_start
            y += row_height + 2
            
        os.remove(temp_path)
        
    return pdf.output(dest='S').encode('latin-1')

# --- 5. CORE LOGIC ---
def add_asset(tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, img_blob, specs):
    # Validation
    tag_valid, tag_msg = validate_asset_tag(tag)
    if not tag_valid:
        return False, tag_msg
    
    if not model or not model.strip():
        return False, "Model cannot be empty"
    
    price_valid, price_msg = validate_price(price)
    if not price_valid:
        return False, price_msg
    
    if p_date:
        date_valid, date_msg = validate_date(str(p_date))
        if not date_valid:
            return False, date_msg
    
    conn = get_connection()
    try:
        img_data = None
        if img_blob:
            try:
                img_data = img_blob.getvalue()
                # Validate image size (max 5MB)
                if len(img_data) > 5 * 1024 * 1024:
                    return False, "Image file is too large (max 5MB)"
            except Exception as e:
                logger.warning(f"Error processing image: {e}")
                img_data = None
        
        sql = '''INSERT INTO assets (asset_tag, category, model, serial_number, status, assigned_to, 
                 purchase_date, price, warranty_date, vendor, last_audit_date, department, image_blob, specs) 
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        conn.execute(sql, (tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, 
                           str(datetime.now().date()), dept, img_data, specs))
        conn.commit()
        log_action(tag, "CREATE", f"เพิ่ม: {model}")
        logger.info(f"Asset created: {tag} - {model}")
        return True, "Success"
    except sqlite3.IntegrityError as e:
        error_msg = "Asset Tag already exists" if "UNIQUE" in str(e) else str(e)
        logger.error(f"Integrity error adding asset {tag}: {e}")
        return False, error_msg
    except Exception as e:
        logger.error(f"Error adding asset {tag}: {e}")
        return False, f"Database error: {str(e)}"
    finally: 
        conn.close()

def update_asset(tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, specs):
    # Validation
    if not model or not model.strip():
        return False, "Model cannot be empty"
    
    price_valid, price_msg = validate_price(price)
    if not price_valid:
        return False, price_msg
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Check if asset exists
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone():
            conn.close()
            return False, "Asset not found"
        
        conn.execute('''UPDATE assets SET category=?, model=?, serial_number=?, status=?, assigned_to=?, 
                        purchase_date=?, price=?, warranty_date=?, vendor=?, department=?, specs=?, last_updated=CURRENT_TIMESTAMP 
                        WHERE asset_tag=?''', 
                     (cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, specs, tag))
        conn.commit()
        log_action(tag, "UPDATE", f"Status: {status}")
        logger.info(f"Asset updated: {tag}")
        return True, "Success"
    except Exception as e:
        logger.error(f"Error updating asset {tag}: {e}")
        conn.rollback()
        return False, f"Update error: {str(e)}"
    finally:
        conn.close()

def process_borrow(tag, borrower, note, signature_blob=None):
    if not borrower or not borrower.strip():
        return False, "Borrower name cannot be empty"
    
    conn = get_connection()
    try:
        # Check if asset exists and is available
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM assets WHERE asset_tag=?", (tag,))
        result = cursor.fetchone()
        if not result:
            return False, "Asset not found"
        if result[0] in ['In Use', 'Repair', 'Lost']:
            return False, f"Asset is not available (Status: {result[0]})"
        
        sig_data = None
        if signature_blob:
            try:
                buf = BytesIO()
                signature_blob.save(buf, format="PNG")
                sig_data = buf.getvalue()
            except Exception as e:
                logger.warning(f"Error processing signature: {e}")

        conn.execute("UPDATE assets SET status='In Use', assigned_to=? WHERE asset_tag=?", (borrower, tag))
        conn.execute("INSERT INTO borrow_logs (asset_tag, borrower_name, action, note, signature_img) VALUES (?, ?, 'BORROW', ?, ?)", 
                     (tag, borrower, note, sig_data))
        conn.commit()
        log_action(tag, "BORROW", f"Borrowed by: {borrower}")
        logger.info(f"Asset {tag} borrowed by {borrower}")
        return True, "Success"
    except Exception as e:
        logger.error(f"Error processing borrow for {tag}: {e}")
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def process_return(tag, note):
    conn = get_connection()
    try:
        # Check if asset exists
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone():
            return False, "Asset not found"
        
        conn.execute("UPDATE assets SET status='In Stock', assigned_to='' WHERE asset_tag=?", (tag,))
        conn.execute("INSERT INTO borrow_logs (asset_tag, borrower_name, action, note) VALUES (?, '', 'RETURN', ?)", (tag, note))
        conn.commit()
        log_action(tag, "RETURN", f"Returned: {note}")
        logger.info(f"Asset {tag} returned")
        return True, "Success"
    except Exception as e:
        logger.error(f"Error processing return for {tag}: {e}")
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def send_repair(tag, vendor, issue):
    if not vendor or not vendor.strip():
        return False, "Vendor name cannot be empty"
    if not issue or not issue.strip():
        return False, "Issue description cannot be empty"
    
    conn = get_connection()
    try:
        # Check if asset exists
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone():
            return False, "Asset not found"
        
        conn.execute("UPDATE assets SET status='Repair', assigned_to=?, last_updated=CURRENT_TIMESTAMP WHERE asset_tag=?", (vendor, tag))
        date_now = str(datetime.now().date())
        conn.execute("INSERT INTO maintenance_logs (asset_tag, vendor, issue, date_sent, status) VALUES (?, ?, ?, ?, 'In Repair')", (tag, vendor, issue, date_now))
        conn.commit()
        log_action(tag, "REPAIR_SEND", f"Sent to: {vendor}")
        logger.info(f"Asset {tag} sent for repair to {vendor}")
        return True, "Success"
    except Exception as e:
        logger.error(f"Error sending repair for {tag}: {e}")
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def finish_repair(tag, cost, note):
    price_valid, price_msg = validate_price(cost)
    if not price_valid:
        return False, price_msg
    
    conn = get_connection()
    try:
        # Check if asset exists
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone():
            return False, "Asset not found"
        
        conn.execute("UPDATE assets SET status='In Stock', assigned_to='', last_updated=CURRENT_TIMESTAMP WHERE asset_tag=?", (tag,))
        date_now = str(datetime.now().date())
        cursor.execute("UPDATE maintenance_logs SET date_received=?, cost=?, status='Completed' WHERE asset_tag=? AND status='In Repair'", (date_now, cost, tag))
        if cursor.rowcount == 0:
            logger.warning(f"No active repair record found for {tag}")
        conn.commit()
        log_action(tag, "REPAIR_FINISH", f"Cost: {cost}")
        logger.info(f"Asset {tag} repair completed, cost: {cost}")
        return True, "Success"
    except Exception as e:
        logger.error(f"Error finishing repair for {tag}: {e}")
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def audit_asset(tag):
    conn = get_connection()
    date_now = str(datetime.now().date())
    conn.execute("UPDATE assets SET last_audit_date=?, last_updated=CURRENT_TIMESTAMP WHERE asset_tag=?", (date_now, tag))
    conn.commit(); conn.close()
    log_action(tag, "AUDIT", f"Checked on {date_now}")

def soft_delete(tag):
    conn = get_connection(); c = conn.cursor()
    row = c.execute("SELECT * FROM assets WHERE asset_tag=?", (tag,)).fetchone()
    if row:
        c.execute("DELETE FROM recycle_bin WHERE asset_tag=?", (tag,))
        # Be careful with indices here if table structure changes. Best to fetch by name or construct explicit dict
        # Assuming standard structure for simplicity as per original code
        try:
             c.execute("INSERT INTO recycle_bin (asset_tag,category,model,serial_number,status,assigned_to,purchase_date,price) VALUES (?,?,?,?,?,?,?,?)", 
                        (row[1],row[2],row[3],row[4],row[5],row[6],row[7],row[8]))
        except: pass # Skip recycle bin if schema mismatch for now
        
        c.execute("DELETE FROM assets WHERE asset_tag=?", (tag,))
        conn.commit(); log_action(tag, "DELETE", "Moved to Bin")
    conn.close()

def restore_asset(tag):
    conn = get_connection(); c = conn.cursor()
    row = c.execute("SELECT * FROM recycle_bin WHERE asset_tag=?", (tag,)).fetchone()
    if row:
        try:
            c.execute("INSERT INTO assets (asset_tag,category,model,serial_number,status,assigned_to,purchase_date,price) VALUES (?,?,?,?,?,?,?,?)", 
                            (row[1],row[2],row[3],row[4],row[5],row[6],row[7],row[8]))
            c.execute("DELETE FROM recycle_bin WHERE asset_tag=?", (tag,))
            conn.commit(); return True, "Success"
        except: return False, "Duplicate"
    conn.close(); return False, "Not Found"

def get_asset_by_tag(tag):
    """Fetches a single asset by its asset tag."""
    conn = get_connection()
    try:
        df = pd.read_sql_query("SELECT * FROM assets WHERE asset_tag=?", conn, params=(tag,))
        if not df.empty:
            return df.iloc[0]
        return None
    except Exception as e:
        logger.error(f"Error fetching asset by tag {tag}: {e}")
        return None
    finally:
        conn.close()

def sync_glpi_data(glpi_computers_df):
    """
    Synchronizes data from a DataFrame of GLPI computers to the local SQLite database.
    It performs an "upsert" operation: updates existing assets and inserts new ones.
    """
    if glpi_computers_df.empty:
        return 0, 0, 0

    success_inserts = 0
    success_updates = 0
    errors = 0
    
    # It seems with expand_dropdowns, the field name doesn't change, but the value becomes the string.
    # We will proceed with the original field names.
    
    for _, row in glpi_computers_df.iterrows():
        asset_tag = row.get('otherserial')
        # Fallback to computer name if 'otherserial' (inventory number) is not set.
        if not asset_tag or asset_tag == 'None':
            asset_tag = row.get('name')

        if not asset_tag or asset_tag == 'None':
            logger.warning(f"Skipping asset from GLPI because it has no 'otherserial' (inventory number) or 'name'. GLPI ID: {row.get('id', 'N/A')}")
            continue # Skip if both otherserial and name are missing
        
        asset_tag = str(asset_tag) # Ensure asset_tag is a string for DB operations

        # Check if asset exists
        existing_asset = get_asset_by_tag(asset_tag)
        
        # Prepare data for DB functions. Use .get() to avoid KeyErrors if a column is missing.
        model = str(row.get('computermodels_id', ''))
        serial = str(row.get('serial', ''))
        # Mapping GLPI's 'Computer Type' to local 'Category'
        category = str(row.get('computertypes_id', 'Other'))
        status = str(row.get('states_id', 'In Stock'))
        # The user is often a name, not just an ID with expand_dropdowns
        assigned_to = str(row.get('users_id', ''))
        vendor = str(row.get('manufacturers_id', ''))
        
        # For fields not typically in the GLPI computer response, we keep existing data or use defaults.
        price = existing_asset['price'] if existing_asset is not None and pd.notnull(existing_asset['price']) else 0.0
        warranty = existing_asset['warranty_date'] if existing_asset is not None else None
        dept = existing_asset['department'] if existing_asset is not None else "Common"
        specs = existing_asset['specs'] if existing_asset is not None else ""
        
        # Handle purchase date, using creation date as a fallback.
        p_date = row.get('date_mod', row.get('date_creation')) # Prefer modification date, fallback to creation
        if p_date and isinstance(p_date, str):
            p_date = p_date.split(" ")[0] # Keep only date part
        else:
            p_date = None

        try:
            if existing_asset is not None:
                # Update existing asset
                success, _ = update_asset(
                    asset_tag, category, model, serial, status, assigned_to, 
                    p_date if p_date else existing_asset['purchase_date'], 
                    price, warranty, vendor, dept, specs
                )
                if success:
                    success_updates += 1
                else:
                    errors += 1
            else:
                # Add new asset
                success, _ = add_asset(
                    asset_tag, category, model, serial, status, assigned_to,
                    p_date, 0.0, None, vendor, "Common", None, "" # Sensible defaults for new assets from GLPI
                )
                if success:
                    success_inserts += 1
                else:
                    errors += 1
        except Exception as e:
            logger.error(f"Error syncing asset {asset_tag}: {e}")
            errors += 1

    return success_inserts, success_updates, errors



# --- 6. MAIN APP ---
from pages import (
    show_dashboard, show_glpi_sync, show_borrow_return, show_maintenance,
    show_audit, show_search, show_manage, show_add_asset, show_qr_code,
    show_logs_reprint, show_bin, show_admin_page
)

# --- 6. MAIN APP ---
def main_app():
    # Sidebar
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
                        try:
                            cookie_manager.delete("asset_auth_token")
                        except KeyError:
                            pass
                        st.session_state['logged_in'] = False
                        st.session_state['username'] = None
                        st.rerun()
                    else:
                        st.error("Incorrect current password.")
    
    if st.sidebar.button("Logout", type="primary"):
        try:
            cookie_manager.delete("asset_auth_token")
        except KeyError:
            pass
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

    # Page routing
    if page == "📈 Dashboard":
        show_dashboard(df)
    elif page == "💻 GLPI Sync":
        show_glpi_sync()
    elif page == "🔁 Borrow/Return":
        show_borrow_return(df)
    elif page == "🔧 Maintenance":
        show_maintenance(df)
    elif page == "✅ Audit":
        show_audit(df)
    elif page == "🔍 Search":
        show_search(df)
    elif page == "🛠️ Manage":
        show_manage(df)
    elif page == "➕ Add":
        show_add_asset()
    elif page == "🖨️ QR Code":
        show_qr_code(df)
    elif page == "📋 Logs & Reprint":
        show_logs_reprint()
    elif page == "🗑️ Bin":
        show_bin()
    elif page == "👨‍💼 Admin":
        show_admin_page()

# --- APP FLOW ---
cookie_user = cookie_manager.get(cookie="asset_auth_token")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

if not st.session_state['logged_in'] and cookie_user and user_exists(cookie_user):
    st.session_state['logged_in'] = True
    st.session_state['username'] = cookie_user

if st.session_state['logged_in']:
    main_app()
else:
    login_page()