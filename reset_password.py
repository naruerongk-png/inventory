"""
Script to reset user passwords in the database.
Run with: python reset_password.py
"""
import sqlite3
import hashlib
import getpass

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def reset_passwords():
    conn = sqlite3.connect("it_inventory.db")
    cursor = conn.cursor()
    
    users_to_reset = ["admin", "user", "it"]
    
    print("Password Reset Utility")
    print("----------------------")
    
    for username in users_to_reset:
        while True:
            print(f"Resetting password for user: '{username}'")
            new_password = getpass.getpass(f"  Enter new password for {username}: ")
            confirm_password = getpass.getpass(f"  Confirm new password for {username}: ")
            
            if new_password and new_password == confirm_password:
                hashed = hash_password(new_password)
                cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed, username))
                print(f"[OK] Password for {username} has been reset.\n")
                break
            else:
                print("[Error] Passwords do not match or are empty. Please try again.\n")
    
    conn.commit()
    conn.close()
    print("Password reset process completed!")

if __name__ == "__main__":
    reset_passwords()