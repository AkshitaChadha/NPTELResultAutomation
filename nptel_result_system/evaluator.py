# evaluator.py
import math
def custom_round(value):

    # Handle NaN safely
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0

    decimal = value - int(value)

    if decimal <= 0.4:
        return int(value)
    else:
        return int(value) + 1



def evaluate_student(student):
    

    external_value = str(student["NPTEL External Marks"]).strip().upper()

    # 🔴 External absent → send to college
    if external_value == "ABSENT":
        student["Track"] = "College"
        student["Result"] = "External Absent"
        return student

    try:
        assignment = float(student["Assignment Marks"])
        external = float(student["NPTEL External Marks"])
    except:
        student["Track"] = "College"
        student["Result"] = "Invalid Marks"
        return student

    # 🔹 Validate limits
    if assignment < 0 or assignment > 25:
        student["Track"] = "College"
        student["Result"] = "Invalid Assignment Marks"
        return student

    if external < 0 or external > 75:
        student["Track"] = "College"
        student["Result"] = "Invalid External Marks"
        return student

    # 🔹 Convert to 40–60
    internal_college = custom_round((assignment / 25) * 40)
    external_college = custom_round((external / 75) * 60)

    total = internal_college + external_college

    # =========================
    # PASS / FAIL + FAIL TAG
    # =========================

    internal_status = ""
    external_status = ""

    if internal_college < 16:
        internal_status = " (FAIL)"

    if external_college < 24:
        external_status = " (FAIL)"

    student["Internal_Final"] = f"{internal_college}{internal_status}"
    student["External_Final"] = f"{external_college}{external_status}"
    student["Total"] = total

    if internal_college >= 16 and external_college >= 24:
        student["Result"] = "PASS"
    else:
        student["Result"] = "FAIL"

    student["Track"] = "NPTEL"

    return student
