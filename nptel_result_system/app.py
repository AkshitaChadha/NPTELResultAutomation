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
from flask import jsonify

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

@app.route("/")
def home():
    return redirect("/login")


@app.route("/upload", methods=["GET", "POST"])
def upload():

    global ORIGINAL_DATA

    if "user_id" not in session:
        return redirect("/login")

    subject_id = request.args.get("subject_id", type=int)

    if subject_id:
        session["active_subject"] = subject_id

    if "active_subject" not in session:
        return "ERROR: active_subject missing in session"

    if "active_session_id" not in session:
        session["session_label"] = get_current_session()
        session["active_session_id"] = get_or_create_session(
            session["session_label"]
        )

    active_session_id = session["active_session_id"]

    # ===============================
    # GET
    # ===============================
    if request.method == "GET":

        conn = get_db_connection()

        subject = conn.execute("""
            SELECT *
            FROM subjects
            WHERE id=? AND session_id=?
        """, (session["active_subject"], active_session_id)).fetchone()

        # 🔍 Check if evaluation already exists
        evaluation = conn.execute("""
            SELECT stage
            FROM evaluations
            WHERE subject_id=? AND session_id=? AND teacher_id=?
            ORDER BY id DESC
            LIMIT 1
        """, (
            session["active_subject"],
            active_session_id,
            session["user_id"]
        )).fetchone()

        conn.close()

        # 🔥 If evaluation exists → Redirect based on stage
        if evaluation:

            stage = evaluation["stage"]

            if stage == "registration_done":
                return redirect(url_for("save_assignment", subject_id=session["active_subject"]))

            elif stage == "assignment_done":
                return redirect(url_for("external_marks", subject_id=session["active_subject"]))

            elif stage in ["external_saved", "college_pending", "college_done"]:
                return redirect(url_for("evaluate", subject_id=session["active_subject"]))

        # 🔹 Otherwise show upload page
        return render_template("upload.html", subject=subject)
    # ===============================
    # POST
    # ===============================

    file = request.files.get("file")

    if not file:
        return "No file uploaded"

    filename = file.filename.lower()

    if filename.endswith(".xlsx"):
        df = pd.read_excel(file, engine="openpyxl")
    elif filename.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        return "Unsupported file format"

    df.columns = df.columns.str.strip().str.lower()

    required_cols = ["sno", "university roll number", "student name"]

    if list(df.columns) != required_cols:
        return "Invalid format. Required columns: sno, university roll number, student name"

    ORIGINAL_DATA = []

    for _, row in df.iterrows():
        ORIGINAL_DATA.append({
            "SNo": row["sno"],
            "University Roll Number": str(row["university roll number"]).strip(),
            "Student Name": row["student name"],
            "Registered": "Registered"
        })

    return render_template("registration_preview.html", data=ORIGINAL_DATA)    

@app.route("/save_registration", methods=["POST"])
def save_registration():

    if "user_id" not in session:
        return redirect("/login")

    subject_id = int(session.get("active_subject"))
    active_session_id = session.get("active_session_id")

    if not subject_id or not active_session_id:
        return "ERROR: session missing"

    rolls = request.form.getlist("roll[]")
    statuses = request.form.getlist("registered[]")

    import json
    conn = get_db_connection()

    # Load ORIGINAL_DATA from memory (first step)
    global ORIGINAL_DATA

    for i in range(len(rolls)):
        for student in ORIGINAL_DATA:
            if student["University Roll Number"] == rolls[i]:
                student["Registered"] = statuses[i]

    # 🔥 CREATE EVALUATION ROW HERE
    existing = conn.execute("""
    SELECT id FROM evaluations
    WHERE subject_id=? AND session_id=? AND teacher_id=?
""", (subject_id, active_session_id, session["user_id"])).fetchone()

    if existing:
        conn.execute("""
            UPDATE evaluations
            SET data_json=?,
                stage='registration_done',
                created_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (json.dumps(ORIGINAL_DATA), existing["id"]))
    else:
        conn.execute("""
            INSERT INTO evaluations
            (subject_id, teacher_id, session_id, data_json, stage, locked)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (
            subject_id,
            session["user_id"],
            active_session_id,
            json.dumps(ORIGINAL_DATA),
            "registration_done"
        ))

    conn.commit()
    conn.close()

    return redirect(url_for("save_assignment", subject_id=subject_id))

@app.route("/save_assignment", methods=["GET", "POST"])
def save_assignment():

    if "user_id" not in session:
        return redirect("/login")

    subject_id = request.args.get("subject_id", type=int)

    if subject_id:
        session["active_subject"] = subject_id
    else:
        subject_id = session.get("active_subject")
    active_session_id = session.get("active_session_id")

    if not subject_id or not active_session_id:
        return "ERROR: session missing"

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    if not evaluation:
        conn.close()
        return redirect(f"/upload?subject_id={subject_id}")

    import json
    data = json.loads(evaluation["data_json"])

    if request.method == "GET":
        conn.close()
        return render_template("assignment_marks.html", data=data)

    for student in data:

        roll = str(student.get("University Roll Number")).strip()
        registered = student.get("Registered", "")

        if registered == "Not Registered":
            student["Assignment Marks"] = 0
            continue

        mark = request.form.get(f"assignment_{roll}", "").strip()

        try:
            mark = float(mark)
            if 0 <= mark <= 25:
                student["Assignment Marks"] = mark
            else:
                student["Assignment Marks"] = 0
        except:
            student["Assignment Marks"] = 0

    conn.execute("""
        UPDATE evaluations
        SET data_json=?,
            stage='assignment_done',
            created_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (json.dumps(data), evaluation["id"]))

    conn.commit()
    conn.close()

    return redirect(url_for("external_marks", subject_id=subject_id))


@app.route("/external_marks", methods=["GET"])
def external_marks():
    if "user_id" not in session:
        return redirect("/login")

    subject_id = request.args.get("subject_id", type=int)

    if subject_id:
        session["active_subject"] = subject_id
    else:
        subject_id = session.get("active_subject")
    active_session_id = session.get("active_session_id")

    if not subject_id or not active_session_id:
        return redirect("/teacher_dashboard")

    conn = get_db_connection()
    evaluation = conn.execute("""
        SELECT data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    conn.close()

    if not evaluation:
        return redirect(f"/upload?subject_id={subject_id}")

    import json
    data = json.loads(evaluation["data_json"]) if evaluation["data_json"] else []

    return render_template("external_marks.html", data=data)

@app.route("/upload_subjects", methods=["GET", "POST"])
def upload_subjects():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    if request.method == "POST":

        file = request.files.get("file")

        if not file:
            return "No file uploaded"

        df = pd.read_excel(file)
        df.columns = df.columns.str.strip().str.lower()

        if list(df.columns) != ["subject_code", "subject_name"]:
            return "Invalid format. Required columns: subject_code, subject_name"

        for _, row in df.iterrows():
            code = str(row["subject_code"]).strip().upper()
            name = str(row["subject_name"]).strip()

            if code and name:
                conn.execute("""
                    INSERT OR IGNORE INTO subjects_master
                    (subject_code, subject_name)
                    VALUES (?, ?)
                """, (code, name))

        conn.commit()
        conn.close()

        # 🔥 Redirect to NPTEL selection
        return redirect("/mark_nptel_subjects")

    subjects = conn.execute("""
        SELECT *
        FROM subjects_master
        ORDER BY subject_code
    """).fetchall()

    conn.close()

    return render_template(
        "upload_subjects.html",
        subjects=subjects
    )

@app.route("/mark_nptel_subjects")
def mark_nptel_subjects():

    if not hod_required():
        return redirect("/login")

    session_id = session["active_session_id"]
    conn = get_db_connection()

    subjects = conn.execute("""
        SELECT sm.*,
        CASE
            WHEN nm.subject_id IS NOT NULL THEN 1
            ELSE 0
        END as selected
        FROM subjects_master sm
        LEFT JOIN nptel_subject_mapping nm
            ON sm.id = nm.subject_id
            AND nm.session_id = ?
        ORDER BY sm.subject_code
    """, (session_id,)).fetchall()

    conn.close()

    return render_template(
        "mark_nptel_subjects.html",
        subjects=subjects
    )

   
@app.route("/map_nptel_subjects", methods=["GET", "POST"])
def map_nptel_subjects():

    if not hod_required():
        return redirect("/login")

    session_id = session["active_session_id"]
    conn = get_db_connection()

    # =========================
    # 🔹 POST → Save Branch + Semester
    # =========================
    if request.method == "POST":

        subject_ids = request.form.getlist("subject_id[]")

        for sid in subject_ids:

            branches = request.form.getlist(f"branches_{sid}")
            semester = request.form.get(f"semester_{sid}")

            # 🔴 delete old mappings for that subject in this session
            conn.execute("""
                DELETE FROM nptel_subject_mapping
                WHERE subject_id = ?
                AND session_id = ?
            """, (sid, session_id))

            for branch in branches:
                conn.execute("""
                    INSERT INTO nptel_subject_mapping
                    (subject_id, branch, semester, session_id)
                    VALUES (?, ?, ?, ?)
                """, (sid, branch, semester, session_id))

        conn.commit()
        conn.close()

        return redirect("/manage_subjects")

    # =========================
    # 🔹 GET → Load ONLY selected subjects
    # =========================
    subjects = conn.execute("""
        SELECT sm.id, sm.subject_code, sm.subject_name
        FROM subjects_master sm
        JOIN nptel_subject_mapping nm
            ON sm.id = nm.subject_id
        WHERE nm.session_id = ?
        GROUP BY sm.id
        ORDER BY sm.subject_code
    """, (session_id,)).fetchall()

    branches = conn.execute("""
        SELECT name
        FROM branches
        WHERE hod_id = ?
    """, (session["user_id"],)).fetchall()

    session_label = session.get("session_label", "").lower()

    if "jan" in session_label or "may" in session_label:
        allowed_semesters = [2, 4, 6, 8]
    else:
        allowed_semesters = [1, 3, 5, 7]

    conn.close()

    return render_template(
        "map_nptel_subjects.html",
        subjects=subjects,
        branches=branches,
        allowed_semesters=allowed_semesters
    )

@app.route("/save_nptel_subjects", methods=["POST"])
def save_nptel_subjects():

    if not hod_required():
        return redirect("/login")

    selected = request.form.getlist("nptel_subjects")
    session_id = session["active_session_id"]

    conn = get_db_connection()

    # 🔴 Delete only this session’s selections
    conn.execute("""
        DELETE FROM nptel_subject_mapping
        WHERE session_id = ?
    """, (session_id,))

    # 🟢 Insert selected subject ids (without branch & semester)
    for sid in selected:
        conn.execute("""
            INSERT INTO nptel_subject_mapping
            (subject_id, session_id)
            VALUES (?, ?)
        """, (sid, session_id))

    conn.commit()
    conn.close()

    return redirect("/map_nptel_subjects")

@app.route("/get_nptel_subjects")
def get_nptel_subjects():
    branch = request.args.get("branch")
    semester = request.args.get("semester")
    session_id = session["active_session_id"]

    conn = get_db_connection()

    subjects = conn.execute("""
    SELECT DISTINCT
        sm.id,
        sm.subject_code,
        sm.subject_name
    FROM nptel_subject_mapping nm
    JOIN subjects_master sm
        ON sm.id = nm.subject_id
    WHERE nm.branch = ?
      AND nm.semester = ?
      AND nm.session_id = ?
    ORDER BY sm.subject_code
""", (branch, semester, session["active_session_id"])).fetchall()

    conn.close()

    return jsonify([
        {
            "id": s["id"],
            "label": f'{s["subject_code"]} - {s["subject_name"]}'
        }
        for s in subjects
    ])


@app.route("/get_available_sections")
def get_available_sections():

    branch = request.args.get("branch")
    semester = int(request.args.get("semester"))

    conn = get_db_connection()

    assigned = conn.execute("""
        SELECT section
        FROM subjects
        WHERE branch = ?
          AND semester = ?
          AND session_id = ?
    """, (branch, semester, session["active_session_id"])).fetchall()

    assigned_sections = {row["section"] for row in assigned}

    all_sections = {"1", "2", "3"}   # extend later if needed
    available_sections = sorted(all_sections - assigned_sections)

    conn.close()

    return jsonify(available_sections)


@app.route("/evaluate")
def evaluate():
    if "user_id" not in session:
        return redirect("/login")

    subject_id = request.args.get("subject_id", type=int)

    if subject_id:
        session["active_subject"] = subject_id
    else:
        subject_id = session.get("active_subject")
        
    active_session_id = session.get("active_session_id")

    if not subject_id or not active_session_id:
        return redirect("/teacher_dashboard")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT *
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    if not evaluation:
        conn.close()
        return redirect(f"/upload?subject_id={subject_id}")

    import json
    data = json.loads(evaluation["data_json"]) if evaluation["data_json"] else []

    nptel_list = []
    college_list = []

    for student in data:

        # Already fully evaluated students
        if student.get("Track") in ["College Evaluated", "NPTEL"]:
            nptel_list.append(student)
            continue

        registered = str(student.get("Registered", "")).strip().lower()
        attendance = str(student.get("Attendance", "Present")).strip()

        if registered != "registered" or attendance == "Absent":
            student["Track"] = "College"
            student["Result"] = "College Exam Required"
            college_list.append(student)
            continue

        try:
            student.setdefault("Assignment Marks", 0)
            student.setdefault("NPTEL External Marks", 0)

            result = evaluate_student(student)

            # 🔥 CRITICAL FIX — store computed values
            student.update(result)

        except:
            student["Track"] = "College"
            student["Result"] = "Invalid Marks"
            college_list.append(student)
            continue

        # Use updated student object
        if student.get("Track") == "College":
            college_list.append(student)
        else:
            nptel_list.append(student)

    # ✅ FINAL MERGE LOGIC
    stage_value = "college_done" if len(college_list) == 0 else "college_pending"

    conn.execute("""
        UPDATE evaluations
        SET data_json=?,
            stage=?,
            created_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (
        json.dumps(data),
        stage_value,
        evaluation["id"]
    ))

    conn.commit()

    unlocked_count = conn.execute("""
        SELECT COUNT(*) as total
        FROM evaluation_records
        WHERE evaluation_id=? AND locked=0
    """, (evaluation["id"],)).fetchone()["total"]

    can_download = (
        evaluation["locked"] == 1
        and unlocked_count == 0
        and stage_value == "college_done"
    )

    conn.close()

    return render_template(
        "result.html",
        data=nptel_list,
        college_list=college_list,
        stage=stage_value,
        evaluation_locked=(evaluation["locked"] == 1),
        unlocked_count=unlocked_count,
        can_download=can_download,
        evaluation_id=evaluation["id"]
        
        
    )
    

@app.route("/reset_evaluation/<int:subject_id>", methods=["POST"])
def reset_evaluation(subject_id):

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    active_session_id = session.get("active_session_id")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, locked
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    if not evaluation:
        conn.close()
        return redirect("/teacher_dashboard")

    # 🚫 Safety: Don't allow reset if locked
    if evaluation["locked"] == 1:
        conn.close()
        flash("Cannot reset. Evaluation is locked.", "danger")
        return redirect("/teacher_dashboard")

    evaluation_id = evaluation["id"]

    # 🔥 Delete child records first
    conn.execute("""
        DELETE FROM evaluation_records
        WHERE evaluation_id=?
    """, (evaluation_id,))

    # 🔥 Delete evaluation row
    conn.execute("""
        DELETE FROM evaluations
        WHERE id=?
    """, (evaluation_id,))

    conn.commit()
    conn.close()

    flash("Evaluation has been reset successfully.", "success")

    return redirect("/teacher_dashboard")


@app.route("/save_external_marks", methods=["POST"])
def save_external_marks():

    if "user_id" not in session:
        return redirect("/login")

    # 🔥 FIX: Read subject_id from URL OR session
    subject_id = request.args.get("subject_id", type=int)

    if subject_id:
        session["active_subject"] = subject_id
    else:
        subject_id = session.get("active_subject")

    active_session_id = session.get("active_session_id")

    if not subject_id or not active_session_id:
        return redirect("/teacher_dashboard")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    if not evaluation:
        conn.close()
        return redirect(f"/upload?subject_id={subject_id}")

    import json
    data = json.loads(evaluation["data_json"]) if evaluation["data_json"] else []

    for student in data:

        roll = str(student.get("University Roll Number", "")).strip()
        registered = student.get("Registered", "")
        status = request.form.get(f"status_{roll}", "Present")

        if registered == "Not Registered":
            student["NPTEL External Marks"] = 0
            student["Attendance"] = "Not Registered"
            continue

        if status == "Absent":
            student["NPTEL External Marks"] = "ABSENT"
            student["Attendance"] = "Absent"
            continue

        mark_input = request.form.get(f"external_{roll}", "").strip()

        try:
            mark = float(mark_input)
            if 0 <= mark <= 75:
                student["NPTEL External Marks"] = mark
            else:
                student["NPTEL External Marks"] = 0
        except:
            student["NPTEL External Marks"] = 0

        student["Attendance"] = "Present"

    conn.execute("""
        UPDATE evaluations
        SET data_json=?,
            stage='external_saved',
            created_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (json.dumps(data), evaluation["id"]))

    conn.commit()
    conn.close()

    # 🔥 FIX: Pass subject_id forward
    return redirect(url_for("evaluate", subject_id=subject_id))
import json

@app.route("/final_results")
def final_results():
    if "user_id" not in session:
        return redirect("/login")

    subject_id = session.get("active_subject")
    active_session_id = session.get("active_session_id")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT *
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    if not evaluation:
        conn.close()
        return redirect("/teacher_dashboard")

    import json
    data = json.loads(evaluation["data_json"])

    final_list = []
    college_pending = []

    for student in data:
        if student.get("Track") in ["College Evaluated", "NPTEL"]:
            final_list.append(student)
        elif student.get("Track") == "College":
            college_pending.append(student)

    stage_value = "college_done" if len(college_pending) == 0 else "college_pending"

    conn.close()

    return render_template(
        "result.html",
        data=final_list,
        college_list=college_pending,
        stage=stage_value,
        evaluation_locked=(evaluation["locked"] == 1),
        unlocked_count=0,
        can_download=False,
        evaluation_id=evaluation["id"]
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

    if "user_id" not in session:
        return redirect("/login")

    # 🔥 FIX: Restore subject_id
    subject_id = request.args.get("subject_id", type=int)

    if subject_id:
        session["active_subject"] = subject_id
    else:
        subject_id = session.get("active_subject")

    active_session_id = session.get("active_session_id")

    if not subject_id or not active_session_id:
        return redirect("/teacher_dashboard")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    if not evaluation:
        conn.close()
        return redirect("/teacher_dashboard")

    import json
    data = json.loads(evaluation["data_json"])

    for student in data:

        if student.get("Track") not in ["College", "College Evaluated"]:
            continue

        roll = str(student["University Roll Number"]).strip()
        status = request.form.get(f"status_{roll}", "Present").strip()
        value = request.form.get(f"external_{roll}", "").strip()

        assignment = float(student.get("Assignment Marks") or 0)
        internal_40 = custom_round((assignment / 25) * 40)
        student["Internal_Converted"] = internal_40

        # 🔴 COLLEGE ABSENT
        if status.upper() == "ABSENT":

            combined_total = internal_40 + 0

            final_internal = custom_round(combined_total * 0.4)
            final_external = custom_round(combined_total * 0.6)

            student["College_External_Raw"] = "ABSENT"
            student["Internal_Final"] = final_internal
            student["External_Final"] = "ABSENT"
            student["Total"] = final_internal + final_external
            student["Result"] = "FAIL"
            student["Track"] = "College Evaluated"

            continue

        try:
            college_external = float(value)
        except:
            continue

        if not (0 <= college_external <= 60):
            continue

        student["College_External_Raw"] = college_external

        combined_total = internal_40 + college_external

        final_internal = custom_round(combined_total * 0.4)
        final_external = custom_round(combined_total * 0.6)

        student["Internal_Final"] = final_internal
        student["External_Final"] = final_external
        student["Total"] = final_internal + final_external
        student["Result"] = (
            "PASS" if final_internal >= 16 and final_external >= 24
            else "FAIL"
        )
        student["Track"] = "College Evaluated"

    conn.execute("""
        UPDATE evaluations
        SET data_json=?,
            stage='college_done',
            created_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (json.dumps(data), evaluation["id"]))

    conn.commit()
    conn.close()

    # 🔥 FIX: Pass subject_id forward
    return redirect(url_for("evaluate", subject_id=subject_id))
@app.route("/download_college_list/<int:eval_id>")
def download_college_list(eval_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()
    evaluation = conn.execute("""
        SELECT data_json
        FROM evaluations
        WHERE id=?
    """, (eval_id,)).fetchone()

    conn.close()

    if not evaluation:
        return "Evaluation not found."

    import json
    data = json.loads(evaluation["data_json"])

    college_students = [
        s for s in data
        if s.get("Track") in ["College", "College Evaluated"]
    ]

    if not college_students:
        return "No college exam students available."

    df = pd.DataFrame(college_students)

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="college_exam_students.xlsx"
    )

@app.route("/login", methods=["GET", "POST"])
def login():

    error = None
    email = ""
    selected_role = "TEACHER"

    if request.method == "POST":

        email = request.form["email"].strip().lower()
        password = request.form["password"]
        selected_role = request.form.get("role")

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
        # 🔴 TEACHER LOGIN
        # =========================
        if selected_role == "TEACHER":

            if teacher and check_password_hash(teacher["password"], password):

                # 🔴 Block Deactivated
                if teacher["is_deactivated"] == 1:
                    error = "Your account is deactivated. Contact HOD."
                    return render_template("login.html", error=error)

                session["user_id"] = teacher["id"]
                session["role"] = "TEACHER"
                session["is_super_admin"] = False
                session.permanent = True

                # First login password change
                if teacher["is_active"] == 0:
                    return redirect("/change_password")

                return redirect("/teacher_dashboard")

            else:
                error = "Invalid email or password"
                return render_template("login.html", error=error)

        # =========================
        # 🔵 ADMIN LOGIN
        # =========================
        elif selected_role == "ADMIN":

            # 🟡 Teacher promoted to Admin
            if teacher and teacher["is_admin"] == 1 \
               and check_password_hash(teacher["password"], password):

                if teacher.get("is_deactivated") == 1:
                    error = "Your account is deactivated. Contact HOD."
                else:
                    session["user_id"] = teacher["id"]
                    session["role"] = "HOD"
                    session["is_super_admin"] = False
                    session.permanent = True

                    if teacher["is_active"] == 0:
                        return redirect("/change_password")

                    return redirect("/hod_dashboard")

            # 🔴 Real HOD (Super Admin)
            elif hod and check_password_hash(hod["password"], password):

                session["user_id"] = hod["id"]
                session["role"] = "HOD"
                session["is_super_admin"] = True
                session.permanent = True

                if hod["is_active"] == 0:
                    return redirect("/change_password")

                return redirect("/hod_dashboard")

            else:
                error = "Invalid email or password"

    return render_template(
        "login.html",
        error=error,
        email=email,
        selected_role=selected_role
    )

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
        WHERE subject_id=? AND session_id=? AND teacher_id=?
    """, (subject_id, active_session_id, session["user_id"]))

    # 2️⃣ Get evaluation_id
    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

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

    conn.execute("""
        UPDATE evaluations
        SET locked = 0
        WHERE id=?
    """, (evaluation_id,))

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

@app.route("/edit_college_marks")
def edit_college_marks():

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    subject_id = session.get("active_subject")

    if not subject_id:
        return redirect("/teacher_dashboard")

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
        return redirect(f"/upload?subject_id={subject_id}")

    if evaluation["locked"] == 1:
        conn.close()
        return "Marks are locked."

    import json
    data = json.loads(evaluation["data_json"]) if evaluation["data_json"] else []

    # 🔥 VERY IMPORTANT FIX
    college_students = [
        s for s in data
        if s.get("Track") in ["College", "College Evaluated"]
        or s.get("Result") == "College Exam Required"
    ]

    conn.close()

    return render_template(
        "edit_college.html",
        data=college_students
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
    teachers_raw = conn.execute("""
    SELECT *
    FROM teachers
    WHERE hod_id=?
    AND is_deactivated=0 AND is_deactivated=0
""", (session["user_id"],)).fetchall()

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
    "SELECT * FROM teachers WHERE id=? AND is_deactivated=0",
    (teacher_id,)
).fetchone()
    
    if teacher["is_deactivated"] == 1:
        conn.close()
        return "Cannot assign subject. Teacher is deactivated."

    if not teacher:
        conn.close()
        flash("Cannot assign subject. Teacher is deactivated.", "danger")
        return redirect("/hod_dashboard")


    # 🔹 Load Branches
    branches = conn.execute(
        "SELECT * FROM branches WHERE hod_id=?",
        (session["user_id"],)
    ).fetchall()

    # 🔹 Load Subjects (from subjects_master)
    subjects = conn.execute("""
        SELECT id, subject_code, subject_name
        FROM subjects_master
        ORDER BY subject_code
    """).fetchall()

    conn.close()

    # =========================
    # 🔴 POST → Save Assignment
    # =========================
    if request.method == "POST":

        subject_id = request.form.get("subject_id")
        semester = request.form.get("semester")
        section = request.form.get("section")
        branch = request.form.get("branch")
        remark = request.form.get("remark")

        conn = get_db_connection()

        try:
            subject_row = conn.execute("""
                SELECT subject_code, subject_name
                FROM subjects_master
                WHERE id=?
            """, (subject_id,)).fetchone()

            if not subject_row:
                return "Invalid Subject Selected."

            subject_code = subject_row["subject_code"]
            subject_name = subject_row["subject_name"]

            # ✅ reuse SAME connection here
            active_session_id = get_or_create_session(
                session["session_label"],
                conn
            )
            existing = conn.execute("""
                SELECT 1
                FROM subjects
                WHERE branch = ?
                AND semester = ?
                AND section = ?
                AND session_id = ?
            """, (branch, semester, section, session["active_session_id"])).fetchone()

            if existing:
                flash(
                    f"Section {section} of {branch} Semester {semester} is already assigned!",
                    "danger"
                )
                conn.close()
                return redirect(request.url)
            
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
    session_label = session["session_label"].lower()

    if "jan" in session_label or "may" in session_label:
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

    conn = get_db_connection()

    # 🔴 Fetch full teacher row ONCE
    teacher = conn.execute(
        "SELECT * FROM teachers WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    # Safety check
    if not teacher:
        conn.close()
        session.clear()
        return redirect("/login")

    # 🔴 Block deactivated teacher
    if teacher["is_deactivated"] == 1:
        conn.close()
        session.clear()
        flash("Your account is deactivated. Contact HOD.", "danger")
        return redirect("/login")

    # ---- Session Setup ----
    if "session_label" not in session:
        session["session_label"] = get_current_session()

    if "active_session_id" not in session:
        session["active_session_id"] = get_or_create_session(
            session["session_label"]
        )

    active_session_id = session["active_session_id"]
    session_label = session["session_label"]
 
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
    WHERE session_id=? AND teacher_id=?
""", (active_session_id, session["user_id"])).fetchall()


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

    print("SUBJECT IDS:", [s["id"] for s in subjects])
    print("EVAL SUBJECT IDS:", [e["subject_id"] for e in evaluations])
    conn.close()

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

    # -------------------------
    # 🔹 Manual Add
    # -------------------------
    if request.method == "POST" and "manual_add" in request.form:
        code = request.form.get("subject_code")
        name = request.form.get("subject_name")

        if code and name:
            conn.execute(
            """
            INSERT OR IGNORE INTO subjects_master (subject_code, subject_name)
            VALUES (?, ?)
            """,
            (code, name)
        )
            conn.commit()

        conn.close()
        return redirect("/manage_subjects")

    # -------------------------
    # 🔹 Excel Upload
    # -------------------------
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
                        """
                        INSERT OR IGNORE INTO subjects_master
                        (subject_code, subject_name)
                        VALUES (?, ?)
                        """,
                        (code, name)
                    )

            conn.commit()

        conn.close()
        return redirect("/mark_nptel_subjects")
    # ==================================================
    # ✅ GET → SHOW MAPPED NPTEL SUBJECTS
    # ==================================================
    session_id = session["active_session_id"]

    subjects = conn.execute("""
        SELECT
            nm.semester,
            sm.id,
            sm.subject_code,
            sm.subject_name,
            GROUP_CONCAT(nm.branch, ', ') AS branches
        FROM nptel_subject_mapping nm
        JOIN subjects_master sm
            ON sm.id = nm.subject_id
        WHERE nm.session_id = ?
        GROUP BY nm.semester, sm.id
        ORDER BY nm.semester, sm.subject_code
    """, (session_id,)).fetchall()

    conn.close()
    from collections import defaultdict

    semester_map = defaultdict(list)

    for row in subjects:
        semester_map[row["semester"]].append(row)

    return render_template(
        "manage_subjects.html",
        semester_map= semester_map
    )

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


from flask import flash
import sqlite3

@app.route("/manage_branches", methods=["GET", "POST"])
def manage_branches():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    if request.method == "POST":

        branch_name = request.form.get("branch_name")

        if branch_name:

            branch_name = branch_name.strip().upper()

            # 🔍 Check if branch already exists for this HOD
            existing = conn.execute("""
                SELECT id FROM branches
                WHERE name=? AND hod_id=?
            """, (branch_name, session["user_id"])).fetchone()

            if existing:
                flash("Branch already exists!", "danger")
            else:
                conn.execute("""
                    INSERT INTO branches (name, hod_id)
                    VALUES (?, ?)
                """, (branch_name, session["user_id"]))
                conn.commit()
                flash("Branch added successfully!", "success")

        conn.close()
        return redirect("/manage_branches")

    # 🔵 GET
    branches = conn.execute("""
        SELECT * FROM branches
        WHERE hod_id=?
        ORDER BY name
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "manage_branches.html",
        branches=branches
    )

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

from flask import flash
import sqlite3

@app.route("/manage_teachers", methods=["GET", "POST"])
def manage_teachers():

    if not hod_required():
        return redirect("/login")

    # Ensure session exists
    if "active_session_id" not in session:
        session["active_session_id"] = get_or_create_session(
            session["session_label"]
        )

    # =========================
    # 🔴 POST SECTION
    # =========================
    if request.method == "POST":

        conn = get_db_connection()

        try:

            # ======================
            # 🔹 Manual Add
            # ======================
            if "manual_add" in request.form:

                name = request.form.get("name")
                email = request.form.get("email")

                if name and email:

                    temp_password = generate_password_hash("TEMP123")

                    try:
                        conn.execute("""
                            INSERT INTO teachers
                            (name, email, password, hod_id, is_active)
                            VALUES (?, ?, ?, ?, 0)
                        """, (
                            name.strip(),
                            email.strip().lower(),
                            temp_password,
                            session["user_id"]
                        ))

                        conn.commit()
                        flash("Teacher added successfully!", "success")

                    except sqlite3.IntegrityError:
                        conn.rollback()
                        flash("Email already exists!", "danger")

            # ======================
            # 🔹 Excel Upload
            # ======================
            if "excel_upload" in request.form:

                file = request.files.get("file")

                if file and file.filename.endswith((".xlsx", ".csv")):

                    if file.filename.endswith(".xlsx"):
                        df = pd.read_excel(file, engine="openpyxl")
                    else:
                        df = pd.read_csv(file)

                    df.columns = df.columns.str.strip()

                    duplicate_found = False

                    for _, row in df.iterrows():

                        name = str(row.get("name", "")).strip()
                        email = str(row.get("email", "")).strip().lower()

                        if name and email:

                            temp_password = generate_password_hash("TEMP123")

                            try:
                                conn.execute("""
                                    INSERT INTO teachers
                                    (name, email, password, hod_id, is_active)
                                    VALUES (?, ?, ?, ?, 0)
                                """, (
                                    name,
                                    email,
                                    temp_password,
                                    session["user_id"]
                                ))

                            except sqlite3.IntegrityError:
                                duplicate_found = True
                                continue

                    conn.commit()

                    if duplicate_found:
                        flash("Some emails already existed and were skipped.", "danger")
                    else:
                        flash("Teachers uploaded successfully!", "success")

        finally:
            conn.close()

        return redirect("/manage_teachers")

    # =========================
    # 🟢 GET SECTION
    # =========================
    conn = get_db_connection()

    teachers = conn.execute("""
    SELECT DISTINCT t.*
    FROM teachers t
    WHERE t.hod_id=?
    ORDER BY 
        t.is_deactivated ASC,   -- Active (0) first, Deactivated (1) last
        t.name ASC
""", (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "manage_teachers.html",
        teachers=teachers,
        is_super_admin=session.get("is_super_admin", False)
    )


@app.route("/toggle_deactivate/<int:teacher_id>", methods=["POST"])
def toggle_deactivate(teacher_id):

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    teacher = conn.execute("""
        SELECT is_deactivated
        FROM teachers
        WHERE id=? AND hod_id=?
    """, (teacher_id, session["user_id"])).fetchone()

    if not teacher:
        conn.close()
        return redirect("/manage_teachers")

    new_value = 0 if teacher["is_deactivated"] == 1 else 1

    conn.execute("""
        UPDATE teachers
        SET is_deactivated=?
        WHERE id=?
    """, (new_value, teacher_id))

    conn.commit()
    conn.close()

    if new_value == 1:
        flash("Teacher deactivated successfully.", "danger")
    else:
        flash("Teacher activated successfully.", "success")

    return redirect("/manage_teachers")

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
@app.route("/download_college_excel/<int:eval_id>")
def download_college_excel(eval_id):
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()
    evaluation = conn.execute("""
        SELECT data_json FROM evaluations WHERE id=?
    """, (eval_id,)).fetchone()

    if not evaluation:
        conn.close()
        return "Evaluation not found."

    import json
    data = json.loads(evaluation["data_json"])

    college_students = [
        s for s in data
        if s.get("Track") in ["College", "College Evaluated"]
    ]

    conn.close()

    if not college_students:
        return "No college students found."

    df = pd.DataFrame(college_students)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="college_exam_students.xlsx"
    )

@app.route("/download_college_pdf/<int:eval_id>")
def download_college_pdf(eval_id):
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()
    evaluation = conn.execute("""
        SELECT data_json FROM evaluations WHERE id=?
    """, (eval_id,)).fetchone()

    if not evaluation:
        conn.close()
        return "Evaluation not found."

    import json
    data = json.loads(evaluation["data_json"])

    college_students = [
        s for s in data
        if s.get("Track") in ["College", "College Evaluated"]
    ]

    conn.close()

    if not college_students:
        return "No college students found."

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesizes.A4)
    elements = []

    table_data = [["Roll No", "Name", "Status"]]
    for s in college_students:
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

@app.route("/download_attendance_sheet/<int:eval_id>")
def download_attendance_sheet(eval_id):

    if "user_id" not in session:
        return redirect("/login")

    conn = get_db_connection()
    evaluation = conn.execute("""
        SELECT data_json
        FROM evaluations
        WHERE id=?
    """, (eval_id,)).fetchone()

    conn.close()

    if not evaluation:
        return "Evaluation not found."

    import json
    data = json.loads(evaluation["data_json"])

    college_students = [
        s for s in data
        if s.get("Track") in ["College", "College Evaluated"]
    ]

    if not college_students:
        return "No students available."

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesizes.A4)
    elements = []

    from reportlab.platypus import Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    styles = getSampleStyleSheet()

    elements.append(Paragraph("<b>DEPARTMENT NAME</b>", styles["Title"]))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Semester: ___", styles["Normal"]))
    elements.append(Paragraph("Section: ___", styles["Normal"]))
    elements.append(Paragraph("Branch: ___", styles["Normal"]))
    elements.append(Spacer(1, 20))

    table_data = [["S.No", "University Roll No", "Student Name", "Signature"]]

    for i, s in enumerate(college_students, start=1):
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

@app.route("/edit_unlocked/<int:subject_id>", methods=["GET", "POST"])
def edit_unlocked(subject_id):

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    active_session_id = session.get("active_session_id")
    if not active_session_id:
        return redirect("/teacher_dashboard")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT id, data_json
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, active_session_id, session["user_id"])).fetchone()

    if not evaluation:
        conn.close()
        return "Evaluation not found."

    import json
    data = json.loads(evaluation["data_json"]) if evaluation["data_json"] else []

    records = conn.execute("""
        SELECT roll_no
        FROM evaluation_records
        WHERE evaluation_id=? AND locked=0
    """, (evaluation["id"],)).fetchall()

    unlocked_rolls = [r["roll_no"] for r in records]

    # -----------------------
    # POST SECTION
    # -----------------------
    if request.method == "POST":

        for student in data:
            roll = str(student.get("University Roll Number")).strip()

            if roll not in unlocked_rolls:
                continue

            if student.get("Track") in ["College", "College Evaluated"]:

                value = request.form.get(f"college_{roll}", "").strip()
                internal_40 = student.get("Internal_Converted", 0)

                # ABSENT
                if value.upper() == "ABSENT":

                    combined_total = internal_40 + 0

                    final_internal = custom_round(combined_total * 0.4)
                    final_external = custom_round(combined_total * 0.6)

                    student["College_External_Raw"] = "ABSENT"
                    student["Internal_Final"] = final_internal
                    student["External_Final"] = "ABSENT"
                    student["Total"] = final_internal + final_external
                    student["Result"] = "FAIL"
                    student["Track"] = "College Evaluated"

                    continue

                try:
                    college_external = float(value)
                except:
                    continue

                if not (0 <= college_external <= 60):
                    continue

                student["College_External_Raw"] = college_external

                combined_total = internal_40 + college_external

                final_internal = custom_round(combined_total * 0.4)
                final_external = custom_round(combined_total * 0.6)

                student["Internal_Final"] = final_internal
                student["External_Final"] = final_external
                student["Total"] = final_internal + final_external

                student["Result"] = (
                    "PASS"
                    if final_internal >= 16 and final_external >= 24
                    else "FAIL"
                )

                student["Track"] = "College Evaluated"

        conn.execute("""
            UPDATE evaluations
            SET data_json=?,
                stage='college_done',
                locked=1,
                created_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (json.dumps(data), evaluation["id"]))

        conn.execute("""
            UPDATE evaluation_records
            SET locked=1
            WHERE evaluation_id=?
        """, (evaluation["id"],))

        conn.commit()
        conn.close()

        return redirect(f"/evaluate?subject_id={subject_id}")

    # -----------------------
    # GET SECTION
    # -----------------------
    filtered_students = [
        s for s in data
        if str(s.get("University Roll Number")) in unlocked_rolls
    ]

    conn.close()

    return render_template(
        "edit_unlocked.html",
        data=filtered_students,
        subject_id=subject_id
    )
    
@app.route("/debug_eval")
def debug_eval():

    if "user_id" not in session:
        return "Not logged in"

    subject_id = session.get("active_subject")
    session_id = session.get("active_session_id")

    conn = get_db_connection()

    evaluation = conn.execute("""
        SELECT *
        FROM evaluations
        WHERE subject_id=? AND session_id=? AND teacher_id=?
        ORDER BY id DESC
        LIMIT 1
    """, (subject_id, session_id, session["user_id"])).fetchone()

    conn.close()

    if not evaluation:
        return "No evaluation found"

    import json
    data = json.loads(evaluation["data_json"])

    # 🔍 Filter by roll number from URL
    roll = request.args.get("roll")

    if roll:
        for student in data:
            if str(student.get("University Roll Number")).strip() == roll:
                return f"<pre>{json.dumps(student, indent=4)}</pre>"

        return "Student not found."

    # If no roll passed → show all
    return f"<pre>{json.dumps(data, indent=4)}</pre>"    
from database import init_db

init_db()

if __name__ == "__main__":
    app.run(debug=True)
