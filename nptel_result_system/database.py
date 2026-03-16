import sqlite3
import json
from werkzeug.security import generate_password_hash

def get_db_connection():
    conn = sqlite3.connect(
        "database.db",
        timeout=30,
        check_same_thread=False
    )
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
        hod_id INTEGER NOT NULL,
        is_active INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        is_deactivated INTEGER DEFAULT 0
    )
    """)

    # =========================
    # SUBJECT MASTER - NOW WITH HOD_ID
    # =========================
    # Check if hod_id column exists, if not add it
    cursor.execute("PRAGMA table_info(subjects_master)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'hod_id' not in columns:
        # Recreate table with hod_id
        cursor.execute("DROP TABLE IF EXISTS subjects_master")
        cursor.execute("""
        CREATE TABLE subjects_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_code TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            hod_id INTEGER NOT NULL,
            UNIQUE(subject_code, hod_id)
        )
        """)
    else:
        # Table already has hod_id, just ensure it's there
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS subjects_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_code TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            hod_id INTEGER NOT NULL,
            UNIQUE(subject_code, hod_id)
        )
        """)

    # =========================
    # BRANCHES
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
    
    -- This allows multiple subjects per section, but prevents duplicate assignments
    UNIQUE(branch, semester, section, subject_code, session_id)
)
""")

    # =========================
    # NPTEL SUBJECT MAPPING
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nptel_subject_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL,
        branch TEXT,
        semester TEXT,
        session_id INTEGER NOT NULL,
        UNIQUE(subject_id, branch, semester, session_id)
    )
    """)

    # =========================
    # EVALUATIONS
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER,
        teacher_id INTEGER,
        session_id INTEGER,
        data_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        stage TEXT,
        locked INTEGER DEFAULT 0,
        unlocked_rolls TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evaluation_students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evaluation_id INTEGER,
        sno TEXT,
        roll_no TEXT,
        student_name TEXT,
        registered TEXT,
        assignment_marks REAL,
        attendance TEXT,
        external_marks REAL,
        track TEXT,
        result TEXT,
        FOREIGN KEY (evaluation_id) REFERENCES evaluations(id)
    )
    """)

    # =========================
    # EVALUATION RECORDS
    # =========================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evaluation_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        evaluation_id INTEGER,
        roll_no TEXT,
        locked INTEGER DEFAULT 1
    )
    """)

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