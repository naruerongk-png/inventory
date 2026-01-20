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
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        asset_tag TEXT, 
        glpi_id INTEGER UNIQUE,
        category TEXT, 
        model TEXT, 
        serial_number TEXT, 
        status TEXT, 
        assigned_to TEXT, 
        purchase_date TEXT, 
        price REAL DEFAULT 0, 
        warranty_date TEXT,
        vendor TEXT,
        last_audit_date TEXT,
        department TEXT,
        image_blob BLOB,
        specs TEXT,
        location TEXT,
        comment TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        glpi_id INTEGER,
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
    
    # --- Ensure Columns Exist (Manual Migration for existing DB) ---
    columns_to_add = [
        ("assets", "glpi_id", "INTEGER"),
        ("assets", "warranty_date", "TEXT"),
        ("assets", "vendor", "TEXT"),
        ("assets", "last_audit_date", "TEXT"),
        ("assets", "department", "TEXT"),
        ("assets", "image_blob", "BLOB"),
        ("assets", "specs", "TEXT"),
        ("recycle_bin", "glpi_id", "INTEGER")
    ]
    
    for table, col, type_ in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {type_}")
        except:
            pass

    conn.commit()
    conn.close()

# --- AUTHENTICATION ---
def hash_password(password):
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
                # Legacy plain text support -> migrate
                if stored_password == password:
                    hashed = hash_password(password)
                    cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed, username))
                    conn.commit()
                    return True
        return False
    except Exception as e:
        logger.error(f"Login error: {e}")
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
        logger.error(f"Change password error: {e}")
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
        logger.error(f"Get users error: {e}")
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
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def delete_user(username):
    if username == 'admin':
        return False, "Cannot delete admin user"
    conn = get_connection()
    try:
        cursor = conn.cursor()
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
        
        for username, stored_password in users:
            if len(stored_password) != 64:
                # If plain text, hash it
                hashed = hash_password(stored_password)
                cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed, username))
                migrated_count += 1
        
        conn.commit()
        if migrated_count > 0:
            logger.info(f"Migrated {migrated_count} passwords to hashed format")
    except Exception as e:
        logger.error(f"Error migrating passwords: {e}")
    finally:
        conn.close()

# --- HELPER FUNCTIONS ---
def validate_asset_tag(tag):
    # อนุญาตให้ Tag เป็นค่าว่างได้ เพื่อรองรับการ Sync จาก GLPI ที่ยังไม่ได้กำหนด Tag
    if tag is None or tag == "":
        return True, ""
    if len(tag) > 50:
        return False, "Asset Tag is too long (max 50 characters)"
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
        tag_str = str(tag) if tag else "Unknown"
        conn.execute("INSERT INTO history (asset_tag, action, details) VALUES (?,?,?)", (tag_str, action, detail))
        conn.commit()
    except Exception as e:
        logger.error(f"Log action error: {e}")
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
def add_asset(tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, img_blob, specs, glpi_id=None):
    if not model or not model.strip(): return False, "Model cannot be empty"
    
    price_valid, price_msg = validate_price(price)
    if not price_valid: return False, price_msg
    
    conn = get_connection()
    try:
        img_data = None
        if img_blob:
            try:
                img_data = img_blob.getvalue()
                if len(img_data) > 5 * 1024 * 1024:
                    return False, "Image file is too large (max 5MB)"
            except: img_data = None
        
        # Check duplicate tag ONLY if tag is provided (not None/Empty)
        if tag:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (tag,))
            if cursor.fetchone():
                return False, f"Asset Tag '{tag}' already exists."

        sql = '''INSERT INTO assets (asset_tag, category, model, serial_number, status, assigned_to, 
                 purchase_date, price, warranty_date, vendor, last_audit_date, department, image_blob, specs, glpi_id) 
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        conn.execute(sql, (tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, 
                           str(datetime.now().date()), dept, img_data, specs, glpi_id))
        conn.commit()
        log_action(tag, "CREATE", f"Add: {model}")
        return True, "Success"
    except sqlite3.IntegrityError as e:
        return False, f"Database Integrity Error: {str(e)}"
    except Exception as e:
        return False, f"Database Error: {str(e)}"
    finally: conn.close()

def update_asset(new_tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, specs, glpi_id=None, original_tag=None):
    if not model or not model.strip(): return False, "Model cannot be empty"
    if not validate_price(price)[0]: return False, "Invalid Price"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # Check duplicate tag if tag changed and not empty
        if new_tag and new_tag != original_tag:
            # Check if this new_tag is used by ANY other asset (that doesn't match our glpi_id)
            # This logic is tricky if we don't have the primary key ID. 
            # We assume glpi_id is unique enough if present.
            cursor.execute("SELECT 1 FROM assets WHERE asset_tag=?", (new_tag,))
            if cursor.fetchone():
                # Potential duplicate. 
                # If we had the row ID it would be safer "WHERE asset_tag=? AND id!=?".
                # For now, let's trust the user or Database Constraint to catch it.
                pass 

        sql = ""
        params = []
        
        # Priority 1: Update by GLPI ID (Most reliable if synced)
        if glpi_id is not None:
            sql = '''UPDATE assets SET asset_tag=?, category=?, model=?, serial_number=?, status=?, assigned_to=?, 
                     purchase_date=?, price=?, warranty_date=?, vendor=?, department=?, specs=?, last_updated=CURRENT_TIMESTAMP
                     WHERE glpi_id=?'''
            params = (new_tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, specs, glpi_id)
        
        # Priority 2: Update by Original Tag (Legacy)
        elif original_tag:
            sql = '''UPDATE assets SET asset_tag=?, category=?, model=?, serial_number=?, status=?, assigned_to=?, 
                     purchase_date=?, price=?, warranty_date=?, vendor=?, department=?, specs=?, last_updated=CURRENT_TIMESTAMP, glpi_id=?
                     WHERE asset_tag=?'''
            params = (new_tag, cat, model, serial, status, assigned, p_date, price, warranty, vendor, dept, specs, glpi_id, original_tag)
        
        else:
            return False, "Cannot identify asset to update (Missing both GLPI ID and Original Tag)"

        conn.execute(sql, params)
        conn.commit()
        log_action(new_tag, "UPDATE", f"Status: {status}")
        return True, "Success"
    except Exception as e:
        conn.rollback()
        return False, f"Update Error: {str(e)}"
    finally: conn.close()

def process_borrow(tag, borrower, note, signature_blob=None):
    if not tag: return False, "Asset has no tag (Cannot borrow)"
    if not borrower: return False, "Borrower name required"
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM assets WHERE asset_tag=?", (tag,))
        result = cursor.fetchone()
        if not result: return False, "Asset not found"
        if result[0] in ['In Use', 'Repair', 'Lost']:
            return False, f"Asset unavailable (Status: {result[0]})"

        sig_data = None
        if signature_blob:
            try:
                buf = BytesIO(); signature_blob.save(buf, format="PNG"); sig_data = buf.getvalue()
            except: pass

        conn.execute("UPDATE assets SET status='In Use', assigned_to=? WHERE asset_tag=?", (borrower, tag))
        conn.execute("INSERT INTO borrow_logs (asset_tag, borrower_name, action, note, signature_img) VALUES (?, ?, 'BORROW', ?, ?)", 
                     (tag, borrower, note, sig_data))
        conn.commit()
        log_action(tag, "BORROW", f"By {borrower}")
        return True, "Success"
    except Exception as e: return False, str(e)
    finally: conn.close()

def process_return(tag, note):
    if not tag: return False, "Asset has no tag"
    conn = get_connection()
    try:
        conn.execute("UPDATE assets SET status='In Stock', assigned_to='' WHERE asset_tag=?", (tag,))
        conn.execute("INSERT INTO borrow_logs (asset_tag, borrower_name, action, note) VALUES (?, '', 'RETURN', ?)", (tag, note))
        conn.commit()
        log_action(tag, "RETURN", note)
        return True, "Success"
    except Exception as e: return False, str(e)
    finally: conn.close()

def send_repair(tag, vendor, issue):
    if not tag: return False, "Asset has no tag"
    if not vendor: return False, "Vendor required"
    conn = get_connection()
    try:
        conn.execute("UPDATE assets SET status='Repair', assigned_to=? WHERE asset_tag=?", (vendor, tag))
        conn.execute("INSERT INTO maintenance_logs (asset_tag, vendor, issue, status, date_sent) VALUES (?, ?, ?, 'In Repair', ?)", 
                     (tag, vendor, issue, str(datetime.now().date())))
        conn.commit()
        log_action(tag, "REPAIR_SEND", vendor)
        return True, "Success"
    except Exception as e: return False, str(e)
    finally: conn.close()

def finish_repair(tag, cost, note):
    if not tag: return False, "Asset has no tag"
    conn = get_connection()
    try:
        conn.execute("UPDATE assets SET status='In Stock', assigned_to='' WHERE asset_tag=?", (tag,))
        date_now = str(datetime.now().date())
        conn.execute("UPDATE maintenance_logs SET cost=?, status='Completed', date_received=? WHERE asset_tag=? AND status='In Repair'", 
                     (cost, date_now, tag))
        conn.commit()
        log_action(tag, "REPAIR_FINISH", f"Cost: {cost}")
        return True, "Success"
    except Exception as e: return False, str(e)
    finally: conn.close()

def audit_asset(tag):
    if not tag: return
    conn = get_connection()
    conn.execute("UPDATE assets SET last_audit_date=?, last_updated=CURRENT_TIMESTAMP WHERE asset_tag=?", (str(datetime.now().date()), tag))
    conn.commit()
    conn.close()
    log_action(tag, "AUDIT", "Audited")

def soft_delete(tag):
    conn = get_connection()
    try:
        # พยายามหาจาก Tag ก่อน
        row = None
        if tag:
            row = conn.execute("SELECT * FROM assets WHERE asset_tag=?", (tag,)).fetchone()
        
        if row:
            # Insert full backup
            conn.execute('''INSERT INTO recycle_bin (asset_tag, glpi_id, category, model, serial_number, status, assigned_to, purchase_date, price) 
                            VALUES (?,?,?,?,?,?,?,?,?)''', 
                            (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9]))
            conn.execute("DELETE FROM assets WHERE asset_tag=?", (tag,))
            conn.commit()
            log_action(tag, "DELETE", "Moved to bin")
    except Exception as e:
        logger.error(f"Soft delete error: {e}")
    finally: conn.close()

def restore_asset(tag):
    conn = get_connection()
    try:
        # Find in Recycle Bin
        row = conn.execute("SELECT * FROM recycle_bin WHERE asset_tag=?", (tag,)).fetchone()
        if row:
            # Restore to Assets
            conn.execute('''INSERT INTO assets (asset_tag, glpi_id, category, model, serial_number, status, assigned_to, purchase_date, price) 
                            VALUES (?,?,?,?,?,?,?,?,?)''', 
                            (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9]))
            conn.execute("DELETE FROM recycle_bin WHERE asset_tag=?", (tag,))
            conn.commit()
            return True, "Success"
        return False, "Not found in bin"
    except Exception as e: return False, str(e)
    finally: conn.close()

def sync_glpi_data(glpi_computers_df):
    if glpi_computers_df.empty: return 0, 0, 0
    success_inserts = 0; success_updates = 0; errors = 0
    
    conn = get_connection()
    cursor = conn.cursor()

    for _, row in glpi_computers_df.iterrows():
        try:
            glpi_id = int(row.get('id'))
        except:
            continue
            
        # Data Mapping
        model = str(row.get('computermodels_id', ''))
        serial = str(row.get('serial', ''))
        category = str(row.get('computertypes_id', 'Other'))
        status = str(row.get('states_id', 'In Stock'))
        assigned_to = str(row.get('users_id', ''))
        vendor = str(row.get('manufacturers_id', ''))
        
        p_date = row.get('date_mod', row.get('date_creation'))
        if p_date and isinstance(p_date, str): 
            p_date = p_date.split(" ")[0]
        else: 
            p_date = None

        # 1. เช็คว่ามี GLPI ID นี้ในระบบเราหรือยัง?
        cursor.execute("SELECT * FROM assets WHERE glpi_id=?", (glpi_id,))
        existing_by_id = cursor.fetchone()

        if existing_by_id:
            # เจอของเดิม: อัปเดตข้อมูลอื่น แต่ *ห้าม* แตะต้อง asset_tag ใน DB 
            try:
                cursor.execute('''UPDATE assets SET category=?, model=?, serial_number=?, status=?, assigned_to=?, 
                                purchase_date=?, vendor=?, last_updated=CURRENT_TIMESTAMP 
                                WHERE glpi_id=?''', 
                            (category, model, serial, status, assigned_to, p_date or existing_by_id[8], vendor, glpi_id))
                success_updates += 1
            except: errors += 1
        else:
            # ไม่เจอ: เป็นเครื่องใหม่
            # ตั้ง Asset Tag เป็น NULL (None) เพื่อให้ User มากรอกเองภายหลัง
            try:
                cursor.execute('''INSERT INTO assets (asset_tag, glpi_id, category, model, serial_number, status, assigned_to, 
                                purchase_date, price, vendor, last_audit_date, department, specs) 
                                VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?)''', 
                            (None, glpi_id, category, model, serial, status, assigned_to, p_date, vendor, str(datetime.now().date()), "Common", ""))
                success_inserts += 1
            except Exception as e:
                errors += 1
    
    conn.commit()
    conn.close()
    return success_inserts, success_updates, errors

# Initialize DB on load
init_and_migrate_db()
migrate_all_passwords_to_hashed()