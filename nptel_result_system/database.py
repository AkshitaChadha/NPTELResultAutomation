import sqlite3
import json
from werkzeug.security import generate_password_hash

def get_db_connection():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():

    conn = get_db_connection()
    cursor = conn.cursor()

    # =========================
    # HODS
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        department TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        is_active INTEGER DEFAULT 0
    )
    """)

    # =========================
    # TEACHERS
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        hod_id INTEGER,
        is_active INTEGER DEFAULT 0
    )
    """)

    # =========================
    # SUBJECTS (SESSION BASED)
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_code TEXT NOT NULL,
        subject_name TEXT NOT NULL,
        semester TEXT,
        section TEXT,
        branch TEXT,
        remark TEXT,
        teacher_id INTEGER,
        session_id INTEGER
    )
    """)

    # =========================
    # SUBJECT MASTER
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects_master (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_code TEXT NOT NULL,
        subject_name TEXT NOT NULL
    )
    """)

    # =========================
    # BRANCHES
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        hod_id INTEGER
    )
    """)

    # =========================
    # SESSIONS TABLE
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT UNIQUE NOT NULL
    )
    """)

    # =========================
    # EVALUATIONS (SESSION BASED)
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER,
        teacher_id INTEGER,
        session_id INTEGER,
        data_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        stage TEXT
    )
    """)

    conn.commit()

    # =========================
    # Load HOD config
    # =========================
    try:
        with open("hod_config.json") as f:
            hod_list = json.load(f)

        for hod in hod_list:
            existing = cursor.execute(
                "SELECT * FROM hods WHERE email=?",
                (hod["email"],)
            ).fetchone()

            if not existing:
                cursor.execute(
                    "INSERT INTO hods (name, department, email, password, is_active) VALUES (?, ?, ?, ?, 0)",
                    (
                        hod["name"],
                        hod["department"],
                        hod["email"],
                        generate_password_hash("TEMP123")
                    )
                )

        conn.commit()

    except Exception as e:
        print("HOD config load error:", e)

    conn.close()
