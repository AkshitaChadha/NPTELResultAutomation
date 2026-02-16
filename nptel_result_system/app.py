from flask import Flask, render_template, request, redirect, url_for, send_file
import pandas as pd
from evaluator import evaluate_student, custom_round
from reportlab.platypus import SimpleDocTemplate, Table
from reportlab.lib import colors
from reportlab.lib import pagesizes
import io
from database import init_db
from flask import session
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

    # 🔐 Security check
    if "user_id" not in session:
        return redirect("/login")

    # =========================
    # 🔵 GET REQUEST
    # =========================
    if request.method == "GET":

        subject_id = request.args.get("subject_id")

        restart = request.args.get("restart")

        if restart == "1":
            conn = get_db_connection()
            conn.execute(
                "DELETE FROM evaluations WHERE subject_id=?",
                (subject_id,)
            )
            conn.commit()
            conn.close()

            return redirect(f"/upload?subject_id={subject_id}")


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
    "SELECT * FROM evaluations WHERE subject_id=?",
    (session["active_subject"],)
).fetchone()


        conn.close()

        # 🔥 If evaluation exists → LOAD RESULT PAGE directly
        if evaluation:

            import json

            ORIGINAL_DATA = json.loads(evaluation["data_json"])

            NPTEL_LIST = []
            COLLEGE_LIST = []

            for student in ORIGINAL_DATA:

                if student.get("Track") in ["College Evaluated", "NPTEL"]:
                    NPTEL_LIST.append(student)

                elif student.get("Track") == "College":
                    COLLEGE_LIST.append(student)

            return render_template(
                "result.html",
                data=NPTEL_LIST,
                college_list=COLLEGE_LIST
            )

        # 🔵 If no evaluation → show upload page
        return render_template("upload.html", subject=subject)

    # =========================
    # 🔴 POST REQUEST (FILE UPLOAD)
    # =========================

    file = request.files["file"]
    filename = file.filename.lower()

    if filename.endswith(".xlsx"):
        df = pd.read_excel(file, engine="openpyxl")

    elif filename.endswith(".csv"):
        df = pd.read_csv(file)

    else:
        return "Unsupported file format. Upload .xlsx or .csv"

    # Remove accidental spaces in headers
    df.columns = df.columns.str.strip()

    ORIGINAL_DATA = df.to_dict(orient="records")

    return render_template("preview.html", data=ORIGINAL_DATA)


@app.route("/start_again/<int:subject_id>")
def start_again(subject_id):

    if "user_id" not in session:
        return redirect("/login")

    from database import get_db_connection

    conn = get_db_connection()

    # delete previous evaluations for this subject
    conn.execute(
        "DELETE FROM evaluations WHERE subject_id=?",
        (subject_id,)
    )

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

        registered = str(student.get("Registered for NPTEL","")).strip().lower()

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
            assignment = float(student.get("Assignment Marks",0))
            external = float(student.get("NPTEL External Marks",0))
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

        existing = conn.execute(
            "SELECT id FROM evaluations WHERE subject_id=?",
            (subject_id,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE evaluations
                SET data_json=?,
                    stage='college_pending',
                    created_at=CURRENT_TIMESTAMP
                WHERE subject_id=?
            """, (
                json.dumps(ORIGINAL_DATA),
                subject_id
            ))
        else:
            conn.execute("""
                INSERT INTO evaluations (subject_id, teacher_id, data_json, stage)
                VALUES (?, ?, ?, ?)
            """, (
                subject_id,
                session["user_id"],
                json.dumps(ORIGINAL_DATA),
                "college_pending"
            ))

        conn.commit()
        conn.close()

    return render_template(
    "result.html",
    data=NPTEL_LIST,
    college_list=COLLEGE_LIST,
    stage="college_pending"
)



@app.route("/edit_college_marks")
def edit_college_marks():

    college_students = [
        s for s in ORIGINAL_DATA
        if s.get("Track") in ["College", "College Evaluated"]
    ]

    return render_template("edit_college.html", data=college_students)




import json

@app.route("/final_results")
def final_results():

    global ORIGINAL_DATA, FINAL_RESULTS

    FINAL_RESULTS = ORIGINAL_DATA.copy()

    return render_template(
    "result.html",
    data=FINAL_RESULTS,
    college_list=[],
    stage="college_done"
)



@app.route("/download_pdf")
def download_pdf():

    global FINAL_RESULTS

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesizes.A4)

    elements = []

    table_data = [["Roll", "Name", "Internal", "External", "Total", "Result"]]

    for s in FINAL_RESULTS:
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
    return send_file(buffer, as_attachment=True, download_name="final_result.pdf")


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
        # ABSENT CASE
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
        # VALIDATION
        # =========================
        try:
            numeric_external = float(external_input)
        except:
            continue

        if numeric_external < 0 or numeric_external > 100:
            continue

        student["College_External_Raw"] = numeric_external

        registered = str(student.get("Registered for NPTEL", "")).strip().lower()

        # NOT REGISTERED
        if registered != "registered":

            total_100 = numeric_external
            internal_40 = custom_round(total_100 * 0.4)
            external_60 = custom_round(total_100 * 0.6)

        else:

            try:
                assignment = float(student.get("Assignment Marks", 0))
            except:
                assignment = 0

            assignment_internal = custom_round((assignment / 25) * 40)
            temp_total = assignment_internal + numeric_external

            internal_40 = custom_round(temp_total * 0.4)
            external_60 = custom_round(temp_total * 0.6)

        internal_status = ""
        external_status = ""

        if internal_40 < 16:
            internal_status = " (FAIL)"

        if external_60 < 24:
            external_status = " (FAIL)"

        student["Internal_Final"] = f"{internal_40}{internal_status}"
        student["External_Final"] = f"{external_60}{external_status}"
        student["Total"] = internal_40 + external_60

        if internal_40 >= 16 and external_60 >= 24:
            student["Result"] = "PASS"
        else:
            student["Result"] = "FAIL"

        # 🔥 FORCE TRACK FIX
        student["Track"] = "College Evaluated"

    # =========================
    # SAVE TO DATABASE (UPSERT STYLE)
    # =========================
    if subject_id:

        import json
        conn = get_db_connection()

        existing = conn.execute(
            "SELECT id FROM evaluations WHERE subject_id=?",
            (subject_id,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE evaluations
                SET data_json=?,
                    stage='college_done',
                    created_at=CURRENT_TIMESTAMP
                WHERE subject_id=?
            """, (
                json.dumps(ORIGINAL_DATA),
                subject_id
            ))
        else:
            conn.execute("""
                INSERT INTO evaluations (subject_id, teacher_id, data_json, stage)
                VALUES (?, ?, ?, ?)
            """, (
                subject_id,
                session["user_id"],
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
        role = request.form["role"]

        conn = get_db_connection()

        if role == "HOD":

            user = conn.execute(
                "SELECT * FROM hods WHERE email=?",
                (email,)
            ).fetchone()

        else:

            user = conn.execute(
                "SELECT * FROM teachers WHERE email=?",
                (email,)
            ).fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["role"] = role
            session.permanent = True


            # 🔥 Force password change
            if user["is_active"] == 0:
                return redirect("/change_password")

            # Redirect based on role
            if role == "HOD":
                return redirect("/hod_dashboard")
            else:
                return redirect("/teacher_dashboard")

        return "Invalid credentials"

    return render_template("login.html")

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

@app.route("/hod_dashboard")
def hod_dashboard():

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    # 🔹 HOD Info
    hod = conn.execute(
        "SELECT * FROM hods WHERE id=?",
        (session["user_id"],)
    ).fetchone()

    # 🔹 Teachers under this HOD
    teachers_raw = conn.execute(
    "SELECT * FROM teachers WHERE hod_id=?",
    (session["user_id"],)
).fetchall()

    # Convert teachers to dict + attach subjects
    teachers = []

    for teacher in teachers_raw:

        teacher_dict = dict(teacher)

        teacher_subjects = conn.execute("""
            SELECT subject_name, subject_code
            FROM subjects
            WHERE teacher_id=?
        """, (teacher["id"],)).fetchall()

        teacher_dict["subjects"] = teacher_subjects

        teachers.append(teacher_dict)


    # 🔹 Subjects under this HOD (via teachers)
    subjects = conn.execute("""
    SELECT s.*, t.name as teacher_name
    FROM subjects s
    JOIN teachers t ON s.teacher_id = t.id
    WHERE t.hod_id = ?
""", (session["user_id"],)).fetchall()


    evaluations = conn.execute("""
    SELECT e.id, e.subject_id, e.stage, e.created_at
    FROM evaluations e
    WHERE e.id IN (
        SELECT MAX(id)
        FROM evaluations
        GROUP BY subject_id
    )
""").fetchall()



    conn.close()

    # =========================
    # 🔹 Map evaluation to subject
    # =========================
    eval_map = {ev["subject_id"]: ev for ev in evaluations}

    # =========================
    # 🔹 Group subjects Branch → Semester (ascending)
    # =========================
    branch_map = {}

    for sub in subjects:

        branch = sub["branch"] or "Unassigned"
        semester = sub["semester"] or "Unassigned"

        # Ensure branch exists
        if branch not in branch_map:
            branch_map[branch] = {}

        # Ensure semester exists inside branch
        if semester not in branch_map[branch]:
            branch_map[branch][semester] = []

        sub_dict = dict(sub)

        if sub["id"] in eval_map:
            sub_dict["stage"] = eval_map[sub["id"]]["stage"]
            sub_dict["evaluation_id"] = eval_map[sub["id"]]["id"]
        else:
            sub_dict["stage"] = "not_started"
            sub_dict["evaluation_id"] = None

        branch_map[branch][semester].append(sub_dict)


    # 🔹 Sort semesters numerically inside each branch
    for branch in branch_map:
        branch_map[branch] = dict(
            sorted(branch_map[branch].items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 999)
        )

    session_label = get_current_session()

    return render_template(
        "hod_dashboard.html",
        hod=hod,
        teachers=teachers,
        branch_map=branch_map,
        session_label=session_label
    )


@app.route("/add_teacher", methods=["GET", "POST"])
def add_teacher():

    if not hod_required():
        return redirect("/login")

    if request.method == "POST":

        name = request.form["name"]
        email = request.form["email"]

        from werkzeug.security import generate_password_hash

        temp_password = generate_password_hash("TEMP123")

        conn = get_db_connection()

        conn.execute(
    "INSERT INTO teachers (name, email, password, hod_id, is_active) VALUES (?, ?, ?, ?, ?)",
    (name, email, temp_password, session["user_id"], 0)
)


        conn.commit()
        conn.close()

        return redirect("/hod_dashboard")

    return render_template("add_teacher.html")

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

    conn.close()

    if not evaluation:
        return "Evaluation not found."

    import json

    data = json.loads(evaluation["data_json"])

    nptel_list = []
    college_list = []

    for student in data:
        if student.get("Track") in ["NPTEL", "College Evaluated"]:
            nptel_list.append(student)
        elif student.get("Track") == "College":
            college_list.append(student)

    return render_template(
    "result.html",
    data=nptel_list,
    college_list=college_list,
    stage=evaluation["stage"]
)




@app.route("/assign_subject/<int:teacher_id>", methods=["GET", "POST"])
def assign_subject(teacher_id):

    if not hod_required():
        return redirect("/login")

    conn = get_db_connection()

    teacher = conn.execute(
        "SELECT * FROM teachers WHERE id=?",
        (teacher_id,)
    ).fetchone()

    if request.method == "POST":

        conn.execute("""
            INSERT INTO subjects
            (subject_code, subject_name, semester, section, branch, remark, teacher_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            request.form["subject_code"],
            request.form["subject_name"],
            request.form["semester"],
            request.form["section"],
            request.form["branch"],
            request.form["remark"],
            teacher_id   # 🔥 MUST BE THIS
        ))

        conn.commit()
        conn.close()
        return redirect("/hod_dashboard")

    conn.close()
    return render_template("assign_subject.html", teacher=teacher)


#teacher dashboard

@app.route("/teacher_dashboard")
def teacher_dashboard():

    if "user_id" not in session or session["role"] != "TEACHER":
        return redirect("/login")

    conn = get_db_connection()

    # 🔹 Fetch subjects assigned to this teacher
    subjects = conn.execute("""
        SELECT *
        FROM subjects
        WHERE teacher_id=?
    """, (session["user_id"],)).fetchall()

    # 🔹 Fetch ONLY latest evaluation per subject (VERY IMPORTANT FIX)
    evaluations = conn.execute("""
    SELECT e.subject_id, e.stage
    FROM evaluations e
    JOIN subjects s ON e.subject_id = s.id
    WHERE s.teacher_id=?
      AND e.id IN (
          SELECT MAX(id)
          FROM evaluations
          GROUP BY subject_id
      )
""", (session["user_id"],)).fetchall()


    conn.close()

    # 🔹 Map subject_id → stage
    eval_map = {ev["subject_id"]: ev["stage"] for ev in evaluations}

    # =========================
    # 🔹 Group subjects Branch → Semester
    # =========================

    branch_map = {}

    for sub in subjects:

        branch = sub["branch"] or "Unassigned"
        semester = sub["semester"] or "Unassigned"

        if branch not in branch_map:
            branch_map[branch] = {}

        if semester not in branch_map[branch]:
            branch_map[branch][semester] = []

        sub_dict = dict(sub)

        # Attach stage safely
        sub_dict["stage"] = eval_map.get(sub["id"], "not_started")

        branch_map[branch][semester].append(sub_dict)

    # 🔹 Sort semesters numerically (ascending)
    for branch in branch_map:
        branch_map[branch] = dict(
            sorted(
                branch_map[branch].items(),
                key=lambda x: int(x[0]) if str(x[0]).isdigit() else 999
            )
        )

    session_label = get_current_session()

    return render_template(
        "teacher_dashboard.html",
        branch_map=branch_map,
        session_label=session_label
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


from database import init_db

init_db()

if __name__ == "__main__":
    app.run(debug=True)
