from database import get_db_connection
from werkzeug.security import generate_password_hash

conn = get_db_connection()

name = "Divya HOD"
department = "CSE"
email = "hodcse@college.edu"
temp_password = generate_password_hash("TEMP123")

conn.execute(
    "INSERT INTO hods (name, department, email, password, is_active) VALUES (?, ?, ?, ?, 0)",
    (name, department, email, temp_password)
)

conn.commit()
conn.close()

print("HOD created with temporary password TEMP123")
