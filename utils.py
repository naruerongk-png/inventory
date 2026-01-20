import sqlite3
import pandas as pd
import hashlib
import logging
import os
import time
import qrcode
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
from fpdf import FPDF
from PIL import Image
# ต้องมีไฟล์ glpi_client.py อยู่ในโฟลเดอร์เดียวกัน
from glpi_client import GlpiApi 

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- DATABASE CONNECTION ---
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
    
    # --- Schema Migrations ---
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
    
    conn.commit()
    conn.close()

# --- AUTHENTICATION ---
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
            if stored_password and len(stored_password) == 64:
                hashed_input = hash_password(password)
                if stored_password == hashed_input:
                    return True
            else:
                if stored_password == password:
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
        cursor.execute("SELECT password FROM users WHERE username=?", (username,))
        result = cursor.fetchone()
        if not result:
            return False

        stored_password = result[0]
        password_match = False
        if len(stored_password) == 64:
            password_match = stored_password == hash_password(old_password)
        else:
            password_match = stored_password == old_password
        
        if password_match:
            hashed_new = hash_password(new_password)
            cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed_new, username))
            conn.commit()
            return True
        else:
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
        return True, "User added successfully"
    except Exception as e:
        logger.error(f"Error adding user {username}: {e}")
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def delete_user(username):
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
        return True, "User deleted successfully"
    except Exception as e:
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def admin_change_user_password(username, new_password):
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
        return True, "Password changed successfully"
    except Exception as e:
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def migrate_all_passwords_to_hashed():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username, password FROM users")
        users = cursor.fetchall()
        migrated_count = 0
        default_passwords = {"admin": "admin", "user": "user", "it": "password"}
        
        for username, stored_password in users:
            if len(stored_password) != 64:
                if username in default_passwords:
                    hashed = hash_password(default_passwords[username])
                    cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed, username))
                    migrated_count += 1
        conn.commit()
        if migrated_count > 0:
            logger.info(f"Migrated {migrated_count} passwords")
    except Exception as e:
        logger.error(f"Error migrating passwords: {e}")
    finally:
        conn.close()

# --- HELPER FUNCTIONS ---
def validate_asset_tag(tag):
    if not tag or not tag.strip():
        return False, "Asset Tag cannot be empty"
    if len(tag) > 50:
        return False, "Asset Tag is too long"
    return True, ""

def validate_price(price):
    try:
        price_float = float(price)
        if price_float < 0:
            return False, "Price cannot be negative"
        return True, ""
    except:
        return False, "Price must be a valid number"

def validate_date(date_str):
    if not date_str:
        return True, ""
    try:
        datetime.strptime(str(date_str), '%Y-%m-%d')
        return True, ""
    except:
        return False, "Invalid date format"

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
    except Exception:
        conn.rollback()
    finally:
        conn.close()

def generate_qr(data):
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf)
    return buf.getvalue()

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

def get_asset_by_tag(tag):
    conn = get_connection()
    try:
        df = pd.read_sql_query("SELECT * FROM assets WHERE asset_tag=?", conn, params=(tag,))
        if not df.empty:
            return df.iloc[0]
        return None
    except Exception:
        return None
    finally:
        conn.close()

# --- PDF GENERATION ---
class PDF(FPDF):
    def header(self):
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

def create_professional_pdf(items_list, user, note, signature_img=None):
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    if os.path.exists("LOGO ARI.png"):
        pdf.image("LOGO ARI.png", 10, 8, 30)
    
    try: pdf.set_font("Thai", 'B', 20)
    except: pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, "IT Asset Handover Form (ใบส่งมอบอุปกรณ์ไอที)", 0, 1, 'C')
    
    try: pdf.set_font("Thai", size=12)
    except: pdf.set_font("Arial", size=10)
    pdf.cell(0, 5, "Official Document / เอกสารสำคัญ", 0, 1, 'C')
    pdf.ln(10)

    pdf.set_fill_color(240, 240, 240)
    pdf.set_draw_color(200, 200, 200)
    
    pdf.cell(130, 8, f" Borrower Name (ชื่อผู้ยืม):  {user}", 1, 0, 'L', fill=True)
    pdf.cell(60, 8, f" Date (วันที่): {datetime.now().strftime('%Y-%m-%d')}", 1, 1, 'L', fill=True)
    pdf.cell(190, 8, f" Note (หมายเหตุ):  {note}", 1, 1, 'L', fill=False)
    pdf.ln(5)

    pdf.set_fill_color(50, 50, 50)
    pdf.set_text_color(255, 255, 255)
    try: pdf.set_font("Thai", 'B', 12)
    except: pdf.set_font("Arial", 'B', 10)
    
    w = [15, 35, 50, 90] 
    pdf.cell(w[0], 8, "No.", 1, 0, 'C', fill=True)
    pdf.cell(w[1], 8, "Asset Tag", 1, 0, 'C', fill=True)
    pdf.cell(w[2], 8, "Model", 1, 0, 'C', fill=True)
    pdf.cell(w[3], 8, "Serial / Specs (รายละเอียด)", 1, 1, 'C', fill=True)
    
    pdf.set_text_color(0, 0, 0)
    try: pdf.set_font("Thai", size=11)
    except: pdf.set_font("Arial", size=10)
    
    for idx, item in enumerate(items_list):
        tag = str(item.get('tag', '-'))
        model = str(item.get('model', '-'))
        serial = str(item.get('serial', '-'))
        specs = str(item.get('specs', ''))
        
        details = f"SN: {serial}"
        if specs and specs != 'None':
            details += f" | {specs}"
        
        def clean(t): return t
        
        pdf.cell(w[0], 8, str(idx+1), 1, 0, 'C')
        pdf.cell(w[1], 8, clean(tag), 1, 0, 'L')
        pdf.cell(w[2], 8, clean(model), 1, 0, 'L')
        if len(details) > 60: details = details[:57] + "..."
        pdf.cell(w[3], 8, clean(details), 1, 1, 'L')

    pdf.ln(10)
    try: pdf.set_font("Thai", size=10)
    except: pass
    pdf.multi_cell(0, 5, "Condition: The borrower acknowledges receipt of the above item(s) in good working condition and agrees to return them upon request.")
    pdf.ln(10)

    y_sig = pdf.get_y()
    pdf.cell(95, 40, "", 1, 0)
    pdf.cell(95, 40, "", 1, 1)
    
    if signature_img is not None:
        temp_path = "temp_signature.png"
        signature_img.save(temp_path)
        pdf.image(temp_path, x=35, y=y_sig+5, w=40) 
        os.remove(temp_path)

    pdf.set_xy(10, y_sig + 32)
    pdf.cell(95, 5, f"Signed by: {user} (Borrower)", 0, 0, 'C')
    pdf.cell(95, 5, "Approved by: IT Support", 0, 1, 'C')
    
    return pdf.output(dest='S').encode('latin-1')

def create_handover_pdf(tag, model, user, note, signature_img=None):
    items_list = [{'tag': tag, 'model': model, 'serial': '-', 'specs': ''}]
    return create_professional_pdf(items_list, user, note, signature_img)

def create_bulk_qr_pdf(data_list):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    
    col_width = 45; row_height = 50
    x_start = 10; y_start = 10
    x = x_start; y = y_start
    
    for i, item in enumerate(data_list):
        qr_img = qrcode.make(f"{item['tag']}\n{item['model']}")
        temp_path = f"temp_qr_{i}.png"
        qr_img.save(temp_path)
        
        if y + row_height > 280:
            pdf.add_page()
            x = x_start; y = y_start
            
        pdf.rect(x, y, col_width, row_height)
        pdf.image(temp_path, x=x+2, y=y+2, w=40, h=40)
        pdf.set_xy(x, y+42)
        
        tag_lbl = str(item['tag']).encode('latin-1', 'ignore').decode('latin-1')
        dept_lbl = str(item['dept']).encode('latin-1', 'ignore').decode('latin-1')
        pdf.multi_cell(col_width, 4, f"{tag_lbl}\n{dept_lbl}", 0, 'C')
        
        x += col_width + 2
        if x + col_width > 200:
            x = x_start; y += row_height + 2
        os.remove(temp_path)
        
    return pdf.output(dest='S').encode('latin-1')

# --- CORE LOGIC ---
def add_asset(tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, img_blob, specs):
    tag_valid, tag_msg = validate_asset_tag(tag)
    if not tag_valid: return False, tag_msg
    if not model or not model.strip(): return False, "Model cannot be empty"
    
    price_valid, price_msg = validate_price(price)
    if not price_valid: return False, price_msg
    
    if p_date:
        if not validate_date(str(p_date))[0]: return False, "Invalid date"
    
    conn = get_connection()
    try:
        img_data = None
        if img_blob:
            try:
                img_data = img_blob.getvalue()
                if len(img_data) > 5 * 1024 * 1024:
                    return False, "Image file is too large (max 5MB)"
            except: img_data = None
        
        sql = '''INSERT INTO assets (asset_tag, category, model, serial_number, status, assigned_to, 
                 purchase_date, price, warranty_date, vendor, last_audit_date, department, image_blob, specs) 
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        conn.execute(sql, (tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, 
                           str(datetime.now().date()), dept, img_data, specs))
        conn.commit()
        log_action(tag, "CREATE", f"เพิ่ม: {model}")
        return True, "Success"
    except sqlite3.IntegrityError:
        return False, "Asset Tag already exists"
    except Exception as e:
        return False, f"Database error: {str(e)}"
    finally: conn.close()

def update_asset(tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, specs):
    if not model or not model.strip(): return False, "Model cannot be empty"
    if not validate_price(price)[0]: return False, "Invalid Price"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone(): return False, "Asset not found"
        
        conn.execute('''UPDATE assets SET category=?, model=?, serial_number=?, status=?, assigned_to=?, 
                        purchase_date=?, price=?, warranty_date=?, vendor=?, department=?, specs=?, last_updated=CURRENT_TIMESTAMP 
                        WHERE asset_tag=?''', 
                     (cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, specs, tag))
        conn.commit()
        log_action(tag, "UPDATE", f"Status: {status}")
        return True, "Success"
    except Exception as e:
        conn.rollback()
        return False, f"Update error: {str(e)}"
    finally: conn.close()

def process_borrow(tag, borrower, note, signature_blob=None):
    if not borrower or not borrower.strip(): return False, "Borrower name cannot be empty"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM assets WHERE asset_tag=?", (tag,))
        result = cursor.fetchone()
        if not result: return False, "Asset not found"
        if result[0] in ['In Use', 'Repair', 'Lost']:
            return False, f"Asset is not available (Status: {result[0]})"
        
        sig_data = None
        if signature_blob:
            try:
                buf = BytesIO()
                signature_blob.save(buf, format="PNG")
                sig_data = buf.getvalue()
            except: pass

        conn.execute("UPDATE assets SET status='In Use', assigned_to=? WHERE asset_tag=?", (borrower, tag))
        conn.execute("INSERT INTO borrow_logs (asset_tag, borrower_name, action, note, signature_img) VALUES (?, ?, 'BORROW', ?, ?)", 
                     (tag, borrower, note, sig_data))
        conn.commit()
        log_action(tag, "BORROW", f"Borrowed by: {borrower}")
        return True, "Success"
    except Exception as e:
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally: conn.close()

def process_return(tag, note):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone(): return False, "Asset not found"
        
        conn.execute("UPDATE assets SET status='In Stock', assigned_to='' WHERE asset_tag=?", (tag,))
        conn.execute("INSERT INTO borrow_logs (asset_tag, borrower_name, action, note) VALUES (?, '', 'RETURN', ?)", (tag, note))
        conn.commit()
        log_action(tag, "RETURN", f"Returned: {note}")
        return True, "Success"
    except Exception as e:
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally: conn.close()

def send_repair(tag, vendor, issue):
    if not vendor: return False, "Vendor name cannot be empty"
    if not issue: return False, "Issue description cannot be empty"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone(): return False, "Asset not found"
        
        conn.execute("UPDATE assets SET status='Repair', assigned_to=?, last_updated=CURRENT_TIMESTAMP WHERE asset_tag=?", (vendor, tag))
        date_now = str(datetime.now().date())
        conn.execute("INSERT INTO maintenance_logs (asset_tag, vendor, issue, date_sent, status) VALUES (?, ?, ?, ?, 'In Repair')", (tag, vendor, issue, date_now))
        conn.commit()
        log_action(tag, "REPAIR_SEND", f"Sent to: {vendor}")
        return True, "Success"
    except Exception as e:
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally: conn.close()

def finish_repair(tag, cost, note):
    if not validate_price(cost)[0]: return False, "Invalid cost"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
        if not cursor.fetchone(): return False, "Asset not found"
        
        conn.execute("UPDATE assets SET status='In Stock', assigned_to='', last_updated=CURRENT_TIMESTAMP WHERE asset_tag=?", (tag,))
        date_now = str(datetime.now().date())
        cursor.execute("UPDATE maintenance_logs SET date_received=?, cost=?, status='Completed' WHERE asset_tag=? AND status='In Repair'", (date_now, cost, tag))
        conn.commit()
        log_action(tag, "REPAIR_FINISH", f"Cost: {cost}")
        return True, "Success"
    except Exception as e:
        conn.rollback()
        return False, f"Error: {str(e)}"
    finally: conn.close()

def audit_asset(tag):
    conn = get_connection()
    date_now = str(datetime.now().date())
    conn.execute("UPDATE assets SET last_audit_date=?, last_updated=CURRENT_TIMESTAMP WHERE asset_tag=?", (date_now, tag))
    conn.commit()
    conn.close()
    log_action(tag, "AUDIT", f"Checked on {date_now}")

def soft_delete(tag):
    conn = get_connection(); c = conn.cursor()
    row = c.execute("SELECT * FROM assets WHERE asset_tag=?", (tag,)).fetchone()
    if row:
        c.execute("DELETE FROM recycle_bin WHERE asset_tag=?", (tag,))
        try:
             c.execute("INSERT INTO recycle_bin (asset_tag,category,model,serial_number,status,assigned_to,purchase_date,price) VALUES (?,?,?,?,?,?,?,?)", 
                        (row[1],row[2],row[3],row[4],row[5],row[6],row[7],row[8]))
        except: pass
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

def sync_glpi_data(glpi_computers_df):
    if glpi_computers_df.empty: return 0, 0, 0
    success_inserts = 0; success_updates = 0; errors = 0
    
    for _, row in glpi_computers_df.iterrows():
        asset_tag = row.get('otherserial')
        if not asset_tag or asset_tag == 'None':
            asset_tag = row.get('name')
        if not asset_tag or asset_tag == 'None': continue
        
        asset_tag = str(asset_tag)
        existing_asset = get_asset_by_tag(asset_tag)
        
        model = str(row.get('computermodels_id', ''))
        serial = str(row.get('serial', ''))
        category = str(row.get('computertypes_id', 'Other'))
        status = str(row.get('states_id', 'In Stock'))
        assigned_to = str(row.get('users_id', ''))
        vendor = str(row.get('manufacturers_id', ''))
        
        price = existing_asset['price'] if existing_asset is not None and pd.notnull(existing_asset['price']) else 0.0
        warranty = existing_asset['warranty_date'] if existing_asset is not None else None
        dept = existing_asset['department'] if existing_asset is not None else "Common"
        specs = existing_asset['specs'] if existing_asset is not None else ""
        
        p_date = row.get('date_mod', row.get('date_creation'))
        if p_date and isinstance(p_date, str): p_date = p_date.split(" ")[0]
        else: p_date = None

        try:
            if existing_asset is not None:
                success, _ = update_asset(asset_tag, category, model, serial, status, assigned_to, 
                    p_date if p_date else existing_asset['purchase_date'], price, warranty, vendor, dept, specs)
                if success: success_updates += 1
                else: errors += 1
            else:
                success, _ = add_asset(asset_tag, category, model, serial, status, assigned_to,
                    p_date, 0.0, None, vendor, "Common", None, "")
                if success: success_inserts += 1
                else: errors += 1
        except Exception: errors += 1

    return success_inserts, success_updates, errors

# Initialize DB on load
init_and_migrate_db()
migrate_all_passwords_to_hashed()