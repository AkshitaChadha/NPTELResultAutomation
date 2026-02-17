import sqlite3
import json
from werkzeug.security import generate_password_hash

def get_db_connection():
    conn = sqlite3.connect(
        "database.db",
        timeout=10,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
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
    #  - email UNIQUE globally
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        hod_id INTEGER NOT NULL,
        is_active INTEGER DEFAULT 0
    )
    """)

    # =========================
    # SUBJECT MASTER
    #  - subject_code UNIQUE
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjects_master (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_code TEXT UNIQUE NOT NULL,
        subject_name TEXT NOT NULL
    )
    """)

    # =========================
    # BRANCHES
    #  - SAME branch cannot repeat under SAME HOD
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        hod_id INTEGER NOT NULL,
        UNIQUE(name, hod_id)
    )
    """)

    # =========================
    # SESSIONS
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT UNIQUE NOT NULL
    )
    """)

    # =========================
    # SUBJECTS (ASSIGNED)
    #  - Same subject can't repeat for same teacher + session + sem + section
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
        teacher_id INTEGER NOT NULL,
        session_id INTEGER NOT NULL,
        UNIQUE(subject_code, teacher_id, session_id, semester, section)
    )
    """)

    # =========================
    # EVALUATIONS
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL,
        teacher_id INTEGER NOT NULL,
        session_id INTEGER NOT NULL,
        data_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        stage TEXT,
        locked INTEGER DEFAULT 0
    )
    """)

    conn.commit()

    # =========================
    # LOAD HOD CONFIG
    # =========================
    try:
        with open("hod_config.json") as f:
            hod_list = json.load(f)

        for hod in hod_list:
            existing = cursor.execute(
                "SELECT id FROM hods WHERE email=?",
                (hod["email"],)
            ).fetchone()

            if not existing:
                cursor.execute("""
                    INSERT INTO hods
                    (name, department, email, password, is_active)
                    VALUES (?, ?, ?, ?, 0)
                """, (
                    hod["name"],
                    hod["department"],
                    hod["email"],
                    generate_password_hash("TEMP123")
                ))

        conn.commit()

    except Exception as e:
        print("HOD config load error:", e)

    conn.close()
