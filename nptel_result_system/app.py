from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
from evaluator import evaluate_student, custom_round
from reportlab.platypus import SimpleDocTemplate, Table
from reportlab.lib import colors
from reportlab.lib import pagesizes
import io
from database import init_db
from flask import session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection
from flask import session 


app = Flask(__name__)

@app.after_request
def add_security_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


from datetime import timedelta

app.secret_key = "super_strong_random_secret_987654321"
app.permanent_session_lifetime = timedelta(minutes=30)


ORIGINAL_DATA = []
NPTEL_LIST = []
COLLEGE_LIST = []
FINAL_RESULTS = []


@app.route("/")
def home():
    return redirect("/login")


@app.route("/upload", methods=["GET", "POST"])
def upload():

    global ORIGINAL_DATA, NPTEL_LIST, COLLEGE_LIST

    if "user_id" not in session:
        return redirect("/login")

    # Ensure session selected
    if "active_session_id" not in session:
        session["session_label"] = get_current_session()
        session["active_session_id"] = get_or_create_session(
            session["session_label"]
        )

    active_session_id = session["active_session_id"]

    if request.method == "GET":

        subject_id = request.args.get("subject_id")

        if subject_id:
            session["active_subject"] = subject_id

        conn = get_db_connection()

        subject = None
        evaluation = None

        if session.get("active_subject"):

            subject = conn.execute(
                "SELECT * FROM subjects WHERE id=?",
                (session["active_subject"],)
            ).fetchone()

            evaluation = conn.execute(
                "SELECT * FROM evaluations WHERE subject_id=? AND session_id=?",
                (session["active_subject"], active_session_id)
            ).fetchone()

        if evaluation and evaluation["stage"] == "college_done":

            import json
            ORIGINAL_DATA = json.loads(evaluation["data_json"])

            NPTEL_LIST = []
            COLLEGE_LIST = []

            for student in ORIGINAL_DATA:
                if student.get("Track") in ["College Evaluated", "NPTEL"]:
                    NPTEL_LIST.append(student)
                elif student.get("Track") == "College":
                    COLLEGE_LIST.append(student)

            # 🔵 Count partial unlock
            unlocked_count = conn.execute("""
                SELECT COUNT(*) as total
                FROM evaluation_records
                WHERE evaluation_id=? AND locked=0
            """, (evaluation["id"],)).fetchone()["total"]

            can_download = (
                evaluation["stage"] == "college_done"
                and evaluation["locked"] == 1
                and unlocked_count == 0
            )

            conn.close()   # ✅ CLOSE AFTER ALL QUERIES

            return render_template(
                "result.html",
                data=NPTEL_LIST,
                college_list=COLLEGE_LIST,
                stage=evaluation["stage"],
                evaluation_locked=(evaluation["locked"] == 1),
                unlocked_count=unlocked_count,
                can_download=can_download,
                evaluation_id=evaluation["id"]
            )

        conn.close()   # ✅ CLOSE HERE if not returning above

        return render_template("upload.html", subject=subject)

        

    # POST (file upload)
    file = request.files["file"]
    filename = file.filename.lower()

    if filename.endswith(".xlsx"):
        df = pd.read_excel(file, engine="openpyxl")
    elif filename.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        return "Unsupported file format."

    df.columns = df.columns.str.strip()
    ORIGINAL_DATA = df.to_dict(orient="records")

    return render_template("preview.html", data=ORIGINAL_DATA)

@app.route("/upload_subjects", methods=["GET", "POST"])
def upload_subjects():

    if not hod_required():
        return redirect("/login")

    session_id = session["active_session_id"]
    conn = get_db_connection()

    if request.method == "POST":

        file = request.files.get("file")
        df = pd.read_excel(file)
        df.columns = df.columns.str.strip().str.lower()

        if list(df.columns) != ["subject_code", "subject_name"]:
            return "Invalid format"

        
        for _, row in df.iterrows():
            conn.execute("""
                INSERT OR IGNORE INTO subjects_master
                (subject_code, subject_name, is_nptel)
                VALUES (?, ?, 0)
            """, (
                str(row["subject_code"]).strip().upper(),
                str(row["subject_name"]).strip()
            ))

        conn.commit()
        conn.close()

        # 🔥 REDIRECT TO NPTEL SELECTION PAGE
        return redirect("/mark_nptel_subjects")

    # 🔵 GET → show only NPTEL subjects
    nptel_subjects = conn.execute("""
        SELECT * FROM subjects_master
        WHERE is_nptel=1
        ORDER BY subject_code
    """).fetchall()

    conn.close()

    return render_template(
        "upload_subjects.html",
        nptel_subjects=nptel_subjects
    )

@app.route("/mark_nptel_subjects")
def mark_nptel_subjects():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()
    session_id = session["active_session_id"]

    subjects = conn.execute("""
        SELECT * FROM subjects_master
        ORDER BY subject_code
    """,).fetchall()

    conn.close()

    return render_template(
        "mark_nptel_subjects.html",
        subjects=subjects
    )
@app.route("/save_nptel_subjects", methods=["POST"])
def save_nptel_subjects():

    if not hod_required():
        return redirect("/login")

    selected = request.form.getlist("nptel_subjects")

    conn = get_db_connection()

    # 🔴 Reset all
    conn.execute("UPDATE subjects_master SET is_nptel = 0")

    # 🟢 Mark selected
    if selected:
        conn.execute(
            f"""
            UPDATE subjects_master
            SET is_nptel = 1
            WHERE id IN ({",".join(["?"] * len(selected))})
            """,
            selected
        )

    conn.commit()
    conn.close()

    # ✅ GO BACK TO MANAGE SUBJECTS
    return redirect("/manage_subjects")

@app.route("/start_again/<int:subject_id>")
def start_again(subject_id):

    if "user_id" not in session:
        return redirect("/login")

    active_session_id = session["active_session_id"]
    conn = get_db_connection()

    locked = conn.execute("""
        SELECT locked
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id)).fetchone()

    if locked and locked["locked"] == 1:
        conn.close()
        return "Marks are locked. You cannot re-evaluate."

    conn.execute("""
        DELETE FROM evaluations
        WHERE subject_id=? AND session_id=?
    """, (subject_id, active_session_id))

    conn.commit()
    conn.close()

    session["active_subject"] = subject_id
    return redirect("/upload")


@app.route("/evaluate", methods=["POST"])
def evaluate():

    global ORIGINAL_DATA, NPTEL_LIST, COLLEGE_LIST

    subject_id = session.get("active_subject")

    rolls = request.form.getlist("roll[]")
    registered_list = request.form.getlist("registered[]")

    # 🔹 Update registration status from preview
    if rolls:
        roll_map = {
            str(s["University Roll Number"]).strip(): s
            for s in ORIGINAL_DATA
        }

        for i in range(len(rolls)):
            roll = str(rolls[i]).strip()

            if roll not in roll_map:
                continue

            student = roll_map[roll]
            student["Registered for NPTEL"] = registered_list[i]

    NPTEL_LIST = []
    COLLEGE_LIST = []

    for student in ORIGINAL_DATA:

        # Skip already college evaluated
        if student.get("Track") in ["College Evaluated", "NPTEL"]:
            NPTEL_LIST.append(student)
            continue

        registered = str(student.get("Registered for NPTEL", "")).strip().lower()
        is_registered = registered == "registered"

        # ===============================
        # CASE 1: NOT REGISTERED
        # ===============================
        if not is_registered:
            student["Track"] = "College"
            student["Result"] = "College Exam Required"
            COLLEGE_LIST.append(student)
            continue

        # ===============================
        # CASE 2: REGISTERED
        # ===============================
        try:
            assignment = float(student.get("Assignment Marks", 0))
            external = float(student.get("NPTEL External Marks", 0))
        except:
            student["Track"] = "College"
            student["Result"] = "College Exam Required"
            COLLEGE_LIST.append(student)
            continue

        # Validate limits
        if assignment > 25 or external > 75:
            student["Track"] = "College"
            student["Result"] = "Invalid Marks"
            COLLEGE_LIST.append(student)
            continue

        result = evaluate_student(student)

        # If student failed NPTEL → College
        if result["Result"] == "FAIL":
            result["Track"] = "College"
            result["Result"] = "College Exam Required"
            COLLEGE_LIST.append(result)
        else:
            NPTEL_LIST.append(result)

    if subject_id:

        import json
        conn = get_db_connection()

        active_session_id = session["active_session_id"]

        existing = conn.execute(
            "SELECT id FROM evaluations WHERE subject_id=? AND session_id=?",
            (subject_id, active_session_id)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE evaluations
                SET data_json=?,
                    stage='college_pending',
                    created_at=CURRENT_TIMESTAMP
                WHERE subject_id=? AND session_id=?
            """, (
                json.dumps(ORIGINAL_DATA),
                subject_id,
                active_session_id
            ))
        else:
            conn.execute("""
                INSERT INTO evaluations
                (subject_id, teacher_id, session_id, data_json, stage)
                VALUES (?, ?, ?, ?, ?)
            """, (
                subject_id,
                session["user_id"],
                active_session_id,
                json.dumps(ORIGINAL_DATA),
                "college_pending"
            ))

        conn.commit()
        conn.close()

    stage_value = "college_done" if len(COLLEGE_LIST) == 0 else "college_pending"

    conn = get_db_connection()

    row = conn.execute("""
        SELECT locked
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, session["active_session_id"])).fetchone()

    conn.close()

    evaluation_locked = row is not None and row["locked"] == 1


    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, locked
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, session["active_session_id"])).fetchone()

    unlocked_count = 0
    can_download = False

    if evaluation:
        unlocked_count = conn.execute("""
            SELECT COUNT(*) as total
            FROM evaluation_records
            WHERE evaluation_id=? AND locked=0
        """, (evaluation["id"],)).fetchone()["total"]

        can_download = (
            stage_value == "college_done"
            and evaluation["locked"] == 1
            and unlocked_count == 0
        )

    conn.close()

    return render_template(
        "result.html",
        data=NPTEL_LIST,
        college_list=COLLEGE_LIST,
        stage=stage_value,
        evaluation_locked=evaluation["locked"] == 1 if evaluation else False,
        unlocked_count=unlocked_count,
        can_download=can_download,
        evaluation_id=evaluation["id"] if evaluation else None
    )



@app.route("/edit_college_marks")
def edit_college_marks():

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    subject_id = session.get("active_subject")
    active_session_id = session["active_session_id"]

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, locked, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id)).fetchone()

    if not evaluation:
        conn.close()
        return "Evaluation not found."

    # 🔴 ONLY allow when fully unlocked
    if evaluation["locked"] == 1:
        conn.close()
        return "Marks are locked. Cannot update."

    import json
    data = json.loads(evaluation["data_json"])

    college_students = [
        s for s in data
        if s.get("Track") in ["College", "College Evaluated"]
    ]

    conn.close()

    return render_template(
        "edit_college.html",
        data=college_students
    )




import json

@app.route("/final_results")
def final_results():

    global ORIGINAL_DATA

    final_list = []
    college_pending = []

    for student in ORIGINAL_DATA:

        # Finalised students
        if student.get("Track") in ["College Evaluated", "NPTEL"]:
            final_list.append(student)

        # Still pending college exam
        elif student.get("Track") == "College":
            college_pending.append(student)

    stage_value = "college_done" if len(college_pending) == 0 else "college_pending"

    return render_template(
    "result.html",
    data=final_list,
    college_list=college_pending,
    stage=stage_value,
    evaluation_locked=False,
    unlocked_count=0,
    can_download=False,
    evaluation_id=None
)





@app.route("/download_pdf/<int:eval_id>")
def download_pdf(eval_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT *
        FROM evaluations
        WHERE id=?
    """, (eval_id,)).fetchone()

    if not evaluation:
        conn.close()
        return "Evaluation not found."

    # 🔵 Count unlocked students
    unlocked_count = conn.execute("""
        SELECT COUNT(*) as total
        FROM evaluation_records
        WHERE evaluation_id=? AND locked=0
    """, (eval_id,)).fetchone()["total"]

    conn.close()

    # 🔒 Allow only if fully locked AND no partial unlock
    if evaluation["locked"] != 1 or unlocked_count > 0:
        return "Result must be fully locked before download."

    import json
    data = json.loads(evaluation["data_json"])

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesizes.A4)

    elements = []
    table_data = [["Roll", "Name", "Internal", "External", "Total", "Result"]]

    for s in data:
        if s.get("Track") in ["NPTEL", "College Evaluated"]:
            table_data.append([
                s.get("University Roll Number"),
                s.get("Student Name"),
                s.get("Internal_Final", "-"),
                s.get("External_Final", "-"),
                s.get("Total", "-"),
                s.get("Result", "-")
            ])

    table = Table(table_data)
    table.setStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ])

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="final_result.pdf"
    )

@app.route("/save_college_marks", methods=["POST"])
def save_college_marks():

    global ORIGINAL_DATA

    subject_id = session.get("active_subject")

    for student in ORIGINAL_DATA:

        if student.get("Track") not in ["College", "College Evaluated"]:
            continue

        roll = str(student["University Roll Number"]).strip()

        status = request.form.get(f"status_{roll}", "Present").strip()
        external_input = request.form.get(f"external_{roll}", "").strip()

        # =========================
        # 🔴 ABSENT CASE
        # =========================
        if status.upper() == "ABSENT":

            student["College_External_Raw"] = "ABSENT"
            student["Internal_Final"] = "0 (FAIL)"
            student["External_Final"] = "ABSENT"
            student["Total"] = 0
            student["Result"] = "FAIL"
            student["Track"] = "College Evaluated"
            continue

        # =========================
        # 🔵 VALIDATION
        # =========================
        try:
            numeric_external = float(external_input)
        except:
            continue

        if numeric_external < 0 or numeric_external > 100:
            continue

        student["College_External_Raw"] = numeric_external

        registered = str(student.get("Registered for NPTEL", "")).strip().lower()

        # ==================================================
        # 🔵 STEP 1: Convert raw marks (NO ROUNDING)
        # ==================================================
        if registered != "registered":
            # Direct 100 mark paper
            base_internal = numeric_external * 0.4
            base_external = numeric_external * 0.6
        else:
            try:
                assignment = float(student.get("Assignment Marks", 0))
            except:
                assignment = 0

            # Convert assignment 25 → 40 (NO ROUND)
            assignment_40 = (assignment / 25) * 40

            # numeric_external already out of 100
            base_internal = assignment_40
            base_external = numeric_external

        # ==================================================
        # 🔵 STEP 2: Make combined total (OUT OF 100)
        # ==================================================
        combined_total = base_internal + base_external

        # ==================================================
        # 🔵 STEP 3: Re-divide 100 → 40/60 (ROUND ONLY HERE)
        # ==================================================
        final_internal = custom_round(combined_total * 0.4)
        final_external = custom_round(combined_total * 0.6)

        total = final_internal + final_external

        # ==================================================
        # 🔵 PASS / FAIL CHECK
        # ==================================================
        internal_status = ""
        external_status = ""

        if final_internal < 16:
            internal_status = " (FAIL)"

        if final_external < 24:
            external_status = " (FAIL)"

        student["Internal_Final"] = f"{final_internal}{internal_status}"
        student["External_Final"] = f"{final_external}{external_status}"
        student["Total"] = total

        if final_internal >= 16 and final_external >= 24:
            student["Result"] = "PASS"
        else:
            student["Result"] = "FAIL"

        student["Track"] = "College Evaluated"

    # ==================================================
    # 🔵 SAVE TO DATABASE
    # ==================================================
    if subject_id:

        import json
        conn = get_db_connection()
        active_session_id = session["active_session_id"]

        existing = conn.execute(
            "SELECT id FROM evaluations WHERE subject_id=? AND session_id=?",
            (subject_id, active_session_id)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE evaluations
                SET data_json=?,
                    stage='college_done',
                    created_at=CURRENT_TIMESTAMP
                WHERE subject_id=? AND session_id=?
            """, (
                json.dumps(ORIGINAL_DATA),
                subject_id,
                active_session_id
            ))
        else:
            conn.execute("""
                INSERT INTO evaluations
                (subject_id, teacher_id, session_id, data_json, stage)
                VALUES (?, ?, ?, ?, ?)
            """, (
                subject_id,
                session["user_id"],
                active_session_id,
                json.dumps(ORIGINAL_DATA),
                "college_done"
            ))

        conn.commit()
        conn.close()

    return redirect(url_for("final_results"))


@app.route("/download_college_list")
def download_college_list():

    global COLLEGE_LIST

    if not COLLEGE_LIST:
        return "No college exam students available."

    df = pd.DataFrame(COLLEGE_LIST)

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="college_exam_students.xlsx"
    )
@app.route("/login", methods=["GET","POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]
        role = request.form.get("role")

        conn = get_db_connection()

        teacher = conn.execute(
            "SELECT * FROM teachers WHERE email=?",
            (email,)
        ).fetchone()

        hod = conn.execute(
            "SELECT * FROM hods WHERE email=?",
            (email,)
        ).fetchone()

        conn.close()

        # =========================
        # TEACHER LOGIN
        # =========================
        if role == "TEACHER":

            if teacher and check_password_hash(teacher["password"], password):

                session["user_id"] = teacher["id"]
                session["role"] = "TEACHER"
                session["is_super_admin"] = False   # 🔥 IMPORTANT
                session.permanent = True

                if teacher["is_active"] == 0:
                    return redirect("/change_password")

                return redirect("/teacher_dashboard")

            return "Invalid teacher credentials"

        # =========================
        # ADMIN LOGIN
        # =========================
        if role == "ADMIN":

            # 🔵 Teacher promoted to admin
            if teacher and teacher["is_admin"] == 1 \
               and check_password_hash(teacher["password"], password):

                session["user_id"] = teacher["id"]
                session["role"] = "HOD"
                session["is_super_admin"] = False   # 🔥 NOT super admin
                session.permanent = True

                if teacher["is_active"] == 0:
                    return redirect("/change_password")

                return redirect("/hod_dashboard")

            # 🔴 Real HOD (Super Admin)
            if hod and check_password_hash(hod["password"], password):

                session["user_id"] = hod["id"]
                session["role"] = "HOD"
                session["is_super_admin"] = True   # 🔥 SUPER ADMIN
                session.permanent = True

                if hod["is_active"] == 0:
                    return redirect("/change_password")

                return redirect("/hod_dashboard")

            return "Invalid admin credentials"

    return render_template("login.html")



@app.route("/check_roles")
def check_roles():

    email = request.args.get("email")

    conn = get_db_connection()

    hod = conn.execute(
        "SELECT id FROM hods WHERE email=?",
        (email,)
    ).fetchone()

    teacher = conn.execute(
        "SELECT is_admin FROM teachers WHERE email=?",
        (email,)
    ).fetchone()

    conn.close()

    roles = []

    # 🔴 Real HOD (Super Admin)
    if hod:
        roles.append("HOD")

    # 🟢 Teacher
    if teacher:
        roles.append("TEACHER")

        # 🟡 Teacher promoted to admin
        if teacher["is_admin"] == 1:
            roles.append("ADMIN")

    return {"roles": roles}


@app.route("/toggle_admin/<int:teacher_id>", methods=["POST"])
def toggle_admin(teacher_id):

    if not hod_required() or not session.get("is_super_admin"):
        return redirect("/login")


    conn = get_db_connection()

    teacher = conn.execute(
        "SELECT is_admin FROM teachers WHERE id=? AND hod_id=?",
        (teacher_id, session["user_id"])
    ).fetchone()

    if not teacher:
        conn.close()
        return redirect("/manage_teachers")

    new_value = 0 if teacher["is_admin"] == 1 else 1

    conn.execute(
        "UPDATE teachers SET is_admin=? WHERE id=?",
        (new_value, teacher_id)
    )

    conn.commit()
    conn.close()

    return redirect("/manage_teachers")



@app.route("/change_password", methods=["GET","POST"])
def change_password():

    if "user_id" not in session:
        return redirect("/login")

    if request.method == "POST":

        new_pass = generate_password_hash(request.form["password"])

        conn = get_db_connection()

        if session["role"] == "HOD":
            conn.execute(
                "UPDATE hods SET password=?, is_active=1 WHERE id=?",
                (new_pass, session["user_id"])
            )
        else:
            conn.execute(
                "UPDATE teachers SET password=?, is_active=1 WHERE id=?",
                (new_pass, session["user_id"])
            )

        conn.commit()
        conn.close()

        return redirect("/login")

    return render_template("change_password.html")


#hod taks

def hod_required():
    if "user_id" not in session or session.get("role") != "HOD":
        return False
    return True

# =========================
# 🔹 Generate All Sessions (From 2022)
# =========================
def get_all_sessions():

    from datetime import datetime

    current_year = datetime.now().year
    sessions = []

    for year in range(2022, current_year + 1):
        sessions.append(f"Jan–May {year}")
        sessions.append(f"July–Nov {year}")

    return list(reversed(sessions))

@app.route("/set_session", methods=["POST"])
def set_session():

    if "user_id" not in session:
        return redirect("/login")

    selected_session = request.form.get("session_label")

    if selected_session:
        session["session_label"] = selected_session
        session["active_session_id"] = get_or_create_session(selected_session)

    # 🔁 Redirect based on role
    if session.get("role") == "HOD":
        return redirect("/hod_dashboard")
    else:
        return redirect("/teacher_dashboard")

@app.route("/lock_marks", methods=["POST"])
def lock_marks():

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    subject_id = session.get("active_subject")
    active_session_id = session["active_session_id"]

    conn = get_db_connection()

    # 1️⃣ Lock master evaluation
    conn.execute("""
        UPDATE evaluations
        SET locked = 1
        WHERE subject_id=? AND session_id=?
    """, (subject_id, active_session_id))

    # 2️⃣ Get evaluation_id
    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id)).fetchone()

    if evaluation:
        import json
        data = json.loads(evaluation["data_json"])

        # 3️⃣ Clear old records (if re-locking)
        conn.execute("""
            DELETE FROM evaluation_records
            WHERE evaluation_id=?
        """, (evaluation["id"],))

        # 4️⃣ Insert all students as locked
        for student in data:
            roll = str(student.get("University Roll Number")).strip()

            conn.execute("""
                INSERT INTO evaluation_records
                (evaluation_id, roll_no, locked)
                VALUES (?, ?, 1)
            """, (evaluation["id"], roll))

    conn.commit()
    conn.close()

    return redirect("/teacher_dashboard")


@app.route("/unlock_marks/<int:subject_id>")
def unlock_marks(subject_id):

    if not hod_required():
        return redirect("/login")

    active_session_id = session["active_session_id"]

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id)).fetchone()

    conn.close()

    if not evaluation:
        return "Evaluation not found."

    import json
    data = json.loads(evaluation["data_json"])

    return render_template(
        "unlock_options.html",
        evaluation_id=evaluation["id"],
        subject_id=subject_id,
        students=data
    )


@app.route("/complete_unlock", methods=["POST"])
def complete_unlock():

    if not hod_required():
        return redirect("/login")

    evaluation_id = request.form.get("evaluation_id")

    conn = get_db_connection()

    # Unlock master evaluation
    conn.execute("""
        UPDATE evaluations
        SET locked = 0
        WHERE id=?
    """, (evaluation_id,))

    # Unlock all student records
    conn.execute("""
        UPDATE evaluation_records
        SET locked = 0
        WHERE evaluation_id=?
    """, (evaluation_id,))

    conn.commit()
    conn.close()

    return redirect("/hod_dashboard")


@app.route("/discard_result", methods=["POST"])
def discard_result():

    if not hod_required():
        return redirect("/login")

    subject_id = request.form.get("subject_id")
    active_session_id = session["active_session_id"]

    conn = get_db_connection()

    # Get evaluation id
    evaluation = conn.execute("""
        SELECT id
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id)).fetchone()

    if evaluation:
        evaluation_id = evaluation["id"]

        # Delete student records
        conn.execute("""
            DELETE FROM evaluation_records
            WHERE evaluation_id=?
        """, (evaluation_id,))

        # Delete evaluation
        conn.execute("""
            DELETE FROM evaluations
            WHERE id=?
        """, (evaluation_id,))

    conn.commit()
    conn.close()

    return redirect("/hod_dashboard")


@app.route("/unlock_selected", methods=["POST"])
def unlock_selected():

    if not hod_required():
        return redirect("/login")

    evaluation_id = request.form.get("evaluation_id")
    selected_rolls = request.form.getlist("selected_rolls")

    if not selected_rolls:
        return redirect("/hod_dashboard")

    conn = get_db_connection()

    for roll in selected_rolls:
        conn.execute("""
            UPDATE evaluation_records
            SET locked = 0
            WHERE evaluation_id=? AND roll_no=?
        """, (evaluation_id, roll))

    conn.commit()
    conn.close()

    return redirect("/hod_dashboard")

@app.route("/edit_unlocked/<int:subject_id>")
def edit_unlocked(subject_id):

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    active_session_id = session["active_session_id"]

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id)).fetchone()

    if not evaluation:
        conn.close()
        return "Evaluation not found."

    records = conn.execute("""
        SELECT roll_no
        FROM evaluation_records
        WHERE evaluation_id=? AND locked=0
    """, (evaluation["id"],)).fetchall()

    unlocked_rolls = [r["roll_no"] for r in records]

    import json
    data = json.loads(evaluation["data_json"])

    # Filter only unlocked students
    filtered_students = [
        s for s in data
        if str(s.get("University Roll Number")) in unlocked_rolls
    ]

    conn.close()

    return render_template(
        "edit_college.html",
        data=filtered_students
    )

@app.route("/hod_dashboard")
def hod_dashboard():

    if not hod_required():
        return redirect("/login")

    # ----------------------------
    # Ensure Session
    # ----------------------------
    if "session_label" not in session:
        session["session_label"] = get_current_session()

    if "active_session_id" not in session:
        session["active_session_id"] = get_or_create_session(
            session["session_label"]
        )

    session_label = session["session_label"]
    active_session_id = session["active_session_id"]

    conn = get_db_connection()

    # ----------------------------
    # HOD Info
    # ----------------------------
    hod = conn.execute(
        "SELECT * FROM hods WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    # ----------------------------
    # Teachers
    # ----------------------------
    teachers_raw = conn.execute(
        "SELECT * FROM teachers WHERE hod_id=?",
        (session["user_id"],)
    ).fetchall()

    teachers = []

    for teacher in teachers_raw:

        teacher_dict = dict(teacher)

        teacher_subjects = conn.execute("""
            SELECT subject_name, subject_code
            FROM subjects
            WHERE teacher_id=? AND session_id=?
        """, (
            teacher["id"],
            active_session_id
        )).fetchall()

        teacher_dict["subjects"] = teacher_subjects
        teachers.append(teacher_dict)

    # ----------------------------
    # Subjects under this HOD
    # ----------------------------
    subjects = conn.execute("""
        SELECT s.*, t.name as teacher_name
        FROM subjects s
        JOIN teachers t ON s.teacher_id = t.id
        WHERE t.hod_id=? AND s.session_id=?
    """, (
        session["user_id"],
        active_session_id
    )).fetchall()

    # ----------------------------
    # Latest Evaluations (per subject)
    # ----------------------------
    evaluations = conn.execute("""
    SELECT e.id, e.subject_id, e.stage, e.locked
    FROM evaluations e
    WHERE e.session_id=?
    AND e.id IN (
        SELECT MAX(id)
        FROM evaluations
        WHERE session_id=?
        GROUP BY subject_id
    )
""", (
    active_session_id,
    active_session_id
)).fetchall()


    # Map subject_id → evaluation row
    eval_map = {
        ev["subject_id"]: ev
        for ev in evaluations
    }

    # ----------------------------
    # Build Branch → Semester Map
    # ----------------------------
    branch_map = {}

    for sub in subjects:

        branch = sub["branch"] or "Unassigned"
        semester = sub["semester"] or "Unassigned"

        if branch not in branch_map:
            branch_map[branch] = {}

        if semester not in branch_map[branch]:
            branch_map[branch][semester] = []

        sub_dict = dict(sub)

        if sub["id"] in eval_map:

            ev = eval_map[sub["id"]]

            sub_dict["stage"] = ev["stage"]
            sub_dict["evaluation_id"] = ev["id"]
            sub_dict["locked"] = ev["locked"] or 0

            # 🔵 Partial unlock count
            # Count unlocked students from evaluation_records
            count = conn.execute("""
                SELECT COUNT(*) as total
                FROM evaluation_records
                WHERE evaluation_id=? AND locked=0
            """, (ev["id"],)).fetchone()["total"]

            sub_dict["partial_unlock_count"] = count

        else:
            sub_dict["stage"] = "not_started"
            sub_dict["evaluation_id"] = None
            sub_dict["locked"] = 0
            sub_dict["partial_unlock_count"] = 0

        branch_map[branch][semester].append(sub_dict)

    # Sort semesters numerically
    for branch in branch_map:
        branch_map[branch] = dict(
            sorted(
                branch_map[branch].items(),
                key=lambda x: int(x[0]) if str(x[0]).isdigit() else 999
            )
        )

    all_sessions = get_all_sessions()

    conn.close()  # ✅ close only once at end

    return render_template(
        "hod_dashboard.html",
        hod=hod,
        teachers=teachers,
        branch_map=branch_map,
        session_label=session_label,
        all_sessions=all_sessions
    )


def get_or_create_session(session_label, conn=None):
    close_conn = False

    if conn is None:
        conn = get_db_connection()
        close_conn = True

    existing = conn.execute(
        "SELECT id FROM sessions WHERE label=?",
        (session_label,)
    ).fetchone()

    if existing:
        if close_conn:
            conn.close()
        return existing["id"]

    conn.execute(
        "INSERT INTO sessions (label) VALUES (?)",
        (session_label,)
    )

    session_id = conn.execute(
        "SELECT id FROM sessions WHERE label=?",
        (session_label,)
    ).fetchone()["id"]

    if close_conn:
        conn.commit()
        conn.close()

    return session_id


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

import json

@app.route("/preview_evaluation/<int:eval_id>")
def preview_evaluation(eval_id):

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT *
        FROM evaluations
        WHERE id=?
    """, (eval_id,)).fetchone()

    if not evaluation:
        conn.close()
        return "Evaluation not found."

    # 🔵 Count partially unlocked students
    unlocked_count = conn.execute("""
        SELECT COUNT(*) as total
        FROM evaluation_records
        WHERE evaluation_id=? AND locked=0
    """, (evaluation["id"],)).fetchone()["total"]

    conn.close()

    import json
    data = json.loads(evaluation["data_json"])

    nptel_list = []
    college_list = []

    for student in data:
        if student.get("Track") in ["NPTEL", "College Evaluated"]:
            nptel_list.append(student)
        elif student.get("Track") == "College":
            college_list.append(student)

    # ✅ FINAL download condition
    can_download = (
        evaluation["stage"] == "college_done"
        and evaluation["locked"] == 1
        and unlocked_count == 0
    )

    return render_template(
        "result.html",
        data=nptel_list,
        college_list=college_list,
        stage=evaluation["stage"],
        evaluation_locked=(evaluation["locked"] == 1),
        unlocked_count=unlocked_count,
        can_download=can_download,
        evaluation_id=evaluation["id"],

    )


@app.route("/assign_subject/<int:teacher_id>", methods=["GET", "POST"])
def assign_subject(teacher_id):

    if not hod_required():
        return redirect("/login")

    # 🔹 Ensure session selected
    if "session_label" not in session:
        session["session_label"] = get_current_session()

    if "active_session_id" not in session:
        session["active_session_id"] = get_or_create_session(
            session["session_label"]
        )

    active_session_id = session["active_session_id"]

    conn = get_db_connection()

    # 🔹 Get teacher
    teacher = conn.execute(
        "SELECT * FROM teachers WHERE id=?",
        (teacher_id,)
    ).fetchone()

    if not teacher:
        conn.close()
        return "Teacher not found."

    # 🔹 Load Subject Master
    subjects = conn.execute(
        "SELECT * FROM subjects_master"
    ).fetchall()

    # 🔹 Load Branches
    branches = conn.execute(
        "SELECT * FROM branches WHERE hod_id=?",
        (session["user_id"],)
    ).fetchall()

    conn.close()

    # =========================
    # 🔴 POST → Save Assignment
    # =========================
    if request.method == "POST":

        subject_code = request.form.get("subject_code")
        semester = request.form.get("semester")
        section = request.form.get("section")
        branch = request.form.get("branch")
        remark = request.form.get("remark")

        conn = get_db_connection()

        try:
            subject_row = conn.execute(
                "SELECT subject_name FROM subjects_master WHERE subject_code=?",
                (subject_code,)
            ).fetchone()

            if not subject_row:
                return "Invalid Subject Selected."

            subject_name = subject_row["subject_name"]

            # ✅ reuse SAME connection here
            active_session_id = get_or_create_session(
                session["session_label"],
                conn
            )

            conn.execute("""
                INSERT INTO subjects
                (subject_code, subject_name, semester, section, branch, remark, teacher_id, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                subject_code,
                subject_name,
                semester,
                section,
                branch,
                remark,
                teacher_id,
                active_session_id
            ))

            conn.commit()

        finally:
            conn.close()

        return redirect("/hod_dashboard")

    # Semester logic
    import datetime
    month = datetime.datetime.now().month

    if 1 <= month <= 5:
        semester_options = [2, 4, 6, 8]
    else:
        semester_options = [1, 3, 5, 7]

    return render_template(
        "assign_subject.html",
        teacher=teacher,
        subjects=subjects,
        semester_options=semester_options,
        branches=branches
    )


#teacher dashboard

@app.route("/teacher_dashboard")
def teacher_dashboard():

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    if "session_label" not in session:
        session["session_label"] = get_current_session()

    if "active_session_id" not in session:
        session["active_session_id"] = get_or_create_session(
            session["session_label"]
        )

    active_session_id = session["active_session_id"]
    session_label = session["session_label"]

    conn = get_db_connection()
    
    teacher = conn.execute(
        "SELECT name FROM teachers WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    subjects = conn.execute("""
        SELECT *
        FROM subjects
        WHERE teacher_id=? AND session_id=?
    """, (
        session["user_id"],
        active_session_id
    )).fetchall()

    evaluations = conn.execute("""
    SELECT id, subject_id, stage, locked
    FROM evaluations
    WHERE session_id=?
""", (active_session_id,)).fetchall()


    eval_map = {}
    unlocked_map = {}

    for ev in evaluations:
        eval_map[ev["subject_id"]] = ev

        # Count unlocked records
        count = conn.execute("""
            SELECT COUNT(*) as total
            FROM evaluation_records
            WHERE evaluation_id=? AND locked=0
        """, (ev["id"],)).fetchone()["total"]

        unlocked_map[ev["subject_id"]] = count


    conn.close()

    eval_map = {ev["subject_id"]: ev for ev in evaluations}

    branch_map = {}

    for sub in subjects:

        branch = sub["branch"] or "Unassigned"
        semester = sub["semester"] or "Unassigned"

        if branch not in branch_map:
            branch_map[branch] = {}

        if semester not in branch_map[branch]:
            branch_map[branch][semester] = []

        sub_dict = dict(sub)

        if sub["id"] in eval_map:
            sub_dict["stage"] = eval_map[sub["id"]]["stage"]
            sub_dict["evaluation_id"] = eval_map[sub["id"]]["id"]
            sub_dict["locked"] = eval_map[sub["id"]]["locked"]
            sub_dict["unlocked_count"] = unlocked_map.get(sub["id"], 0)
        else:
            sub_dict["stage"] = "not_started"
            sub_dict["evaluation_id"] = None
            sub_dict["locked"] = 0


        branch_map[branch][semester].append(sub_dict)

    return render_template(
    "teacher_dashboard.html",
    branch_map=branch_map,
    session_label=session_label,
    all_sessions=get_all_sessions(),
    teacher=teacher
)



from datetime import datetime

def get_current_session():

    now = datetime.now()
    year = now.year
    month = now.month

    if month <= 5:
        return f"Jan–May {year}"
    elif month <= 11:
        return f"July–Nov {year}"
    else:
        return f"Jan–May {year+1}"


@app.route("/manage_subjects", methods=["GET", "POST"])
def manage_subjects():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    # 🔹 Manual Add
    if request.method == "POST" and "manual_add" in request.form:

        code = request.form.get("subject_code")
        name = request.form.get("subject_name")

        if code and name:
            conn.execute(
                "INSERT INTO subjects_master (subject_code, subject_name) VALUES (?, ?)",
                (code.strip().upper(), name.strip())
            )
            conn.commit()

        return redirect("/manage_subjects")

    # 🔹 Excel Upload
    if request.method == "POST" and "excel_upload" in request.form:

        file = request.files.get("file")

        if file and file.filename.endswith((".xlsx", ".csv")):

            if file.filename.endswith(".xlsx"):
                df = pd.read_excel(file, engine="openpyxl")
            else:
                df = pd.read_csv(file)

            df.columns = df.columns.str.strip()

            for _, row in df.iterrows():
                code = str(row.get("subject_code", "")).strip().upper()
                name = str(row.get("subject_name", "")).strip()

                if code and name:
                    conn.execute(
                        "INSERT INTO subjects_master (subject_code, subject_name) VALUES (?, ?)",
                        (code, name)
                    )

            conn.commit()

        return redirect("/manage_subjects")

    subjects = conn.execute(
        """
        SELECT *
        FROM subjects_master
        WHERE is_nptel = 1
        ORDER BY subject_code
        """
    ).fetchall()

    conn.close()

    return render_template("manage_subjects.html", subjects=subjects)


@app.route("/delete_subject/<int:subject_id>")
def delete_subject(subject_id):

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    conn.execute(
        "DELETE FROM subjects_master WHERE id=?",
        (subject_id,)
    )

    conn.commit()
    conn.close()

    return redirect("/manage_subjects")


@app.route("/manage_branches", methods=["GET", "POST"])
def manage_branches():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    # Add Branch
    if request.method == "POST":
        branch_name = request.form.get("branch_name")

        if branch_name:
                conn.execute(
                    "INSERT INTO branches (name, hod_id) VALUES (?, ?)",
                    (branch_name.strip().upper(), session["user_id"])
                )
                conn.commit()


        return redirect("/manage_branches")

    branches = conn.execute(
        "SELECT * FROM branches WHERE hod_id=? ORDER BY name",
        (session["user_id"],)
    ).fetchall()

    conn.close()

    return render_template("manage_branches.html", branches=branches)


@app.route("/delete_branch/<int:branch_id>")
def delete_branch(branch_id):

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    try:
        conn.execute(
            "DELETE FROM branches WHERE id=? AND hod_id=?",
            (branch_id, session["user_id"])
        )
        conn.commit()

    except Exception as e:
        print("Delete branch error:", e)

    finally:
        conn.close()   # 🔥 ALWAYS runs

    return redirect("/manage_branches")


@app.route("/hod_profile")
def hod_profile():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    # Branches
    branches = conn.execute(
        "SELECT * FROM branches WHERE hod_id=? ORDER BY name",
        (session["user_id"],)
    ).fetchall()

    # Subjects Master
    subjects = conn.execute(
        "SELECT * FROM subjects_master ORDER BY subject_code"
    ).fetchall()

    # Teachers
    teachers = conn.execute(
        "SELECT * FROM teachers WHERE hod_id=? ORDER BY name",
        (session["user_id"],)
    ).fetchall()

    conn.close()

    return render_template(
        "hod_profile.html",
        branches=branches,
        subjects=subjects,
        teachers=teachers
    )

@app.route("/manage_teachers", methods=["GET", "POST"])
def manage_teachers():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    # Manual Add
    if request.method == "POST" and "manual_add" in request.form:

        name = request.form.get("name")
        email = request.form.get("email")

        if name and email:

            temp_password = generate_password_hash("TEMP123")

            conn.execute("""
                INSERT INTO teachers (name, email, password, hod_id, is_active)
                VALUES (?, ?, ?, ?, 0)
            """, (
                name.strip(),
                email.strip(),
                temp_password,
                session["user_id"]
            ))

            conn.commit()

        return render_template(
    "manage_teachers.html",
    teachers=teachers,
    is_super_admin=session.get("is_super_admin", False)

)


    # Excel Upload
    if request.method == "POST" and "excel_upload" in request.form:

        file = request.files.get("file")

        if file and file.filename.endswith((".xlsx", ".csv")):

            if file.filename.endswith(".xlsx"):
                df = pd.read_excel(file, engine="openpyxl")
            else:
                df = pd.read_csv(file)

            df.columns = df.columns.str.strip()

            for _, row in df.iterrows():

                name = str(row.get("name", "")).strip()
                email = str(row.get("email", "")).strip()

                if name and email:

                    temp_password = generate_password_hash("TEMP123")

                    conn.execute("""
                        INSERT INTO teachers (name, email, password, hod_id, is_active)
                        VALUES (?, ?, ?, ?, 0)
                    """, (
                        name,
                        email,
                        temp_password,
                        session["user_id"]
                    ))

            conn.commit()

        return render_template(
    "manage_teachers.html",
    teachers=teachers,
    is_super_admin=session.get("is_super_admin", False)
)


    teachers = conn.execute(
        "SELECT * FROM teachers WHERE hod_id=? ORDER BY name",
        (session["user_id"],)
    ).fetchall()

    conn.close()

    return render_template(
    "manage_teachers.html",
    teachers=teachers,
    is_super_admin=session.get("is_super_admin", False)
)



@app.route("/delete_teacher/<int:teacher_id>")
def delete_teacher(teacher_id):

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    conn.execute(
        "DELETE FROM teachers WHERE id=? AND hod_id=?",
        (teacher_id, session["user_id"])
    )

    conn.commit()
    conn.close()

    return redirect("/manage_teachers")

#download options 
@app.route("/download_college_excel")
def download_college_excel():

    if not COLLEGE_LIST:
        return "No college exam students available."

    df = pd.DataFrame(COLLEGE_LIST)

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="college_exam_students.xlsx"
    )

@app.route("/download_college_pdf")
def download_college_pdf():

    if not COLLEGE_LIST:
        return "No college exam students available."

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesizes.A4)

    elements = []

    table_data = [["Roll No", "Name", "Status"]]

    for s in COLLEGE_LIST:
        table_data.append([
            s.get("University Roll Number"),
            s.get("Student Name"),
            s.get("Result")
        ])

    table = Table(table_data)
    table.setStyle([('GRID', (0,0), (-1,-1), 1, colors.black)])

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="college_exam_students.pdf"
    )


@app.route("/download_attendance_sheet")
def download_attendance_sheet():

    if not COLLEGE_LIST:
        return "No students available."

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesizes.A4)

    elements = []

    from reportlab.platypus import Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()

    # 🔥 Big Department Heading
    elements.append(Paragraph("<b>DEPARTMENT NAME</b>", styles["Title"]))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Semester: ___", styles["Normal"]))
    elements.append(Paragraph("Section: ___", styles["Normal"]))
    elements.append(Paragraph("Branch: ___", styles["Normal"]))
    elements.append(Spacer(1, 20))

    # Table
    table_data = [["S.No", "University Roll No", "Student Name", "Signature"]]

    for i, s in enumerate(COLLEGE_LIST, start=1):
        table_data.append([
            i,
            s.get("University Roll Number"),
            s.get("Student Name"),
            ""
        ])

    table = Table(table_data, colWidths=[40, 120, 150, 120])
    table.setStyle([('GRID', (0,0), (-1,-1), 1, colors.black)])

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="attendance_sheet.pdf"
    )



from database import init_db

init_db()

if __name__ == "__main__":
    app.run(debug=True)
