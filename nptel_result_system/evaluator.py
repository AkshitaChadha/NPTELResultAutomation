# evaluator.py

import math


def custom_round(value):
    """
    Rounding rule:
    < 0.5 → floor
    >= 0.5 → ceil
    """

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0

    decimal = value - int(value)

    if decimal < 0.5:
        return int(value)
    else:
        return int(value) + 1


def evaluate_student(student):
    """
    NPTEL STAGE LOGIC

    1. Convert 25 → 40
    2. Convert 75 → 60
    3. Check eligibility on converted values
    4. If PASS → combine & re-divide
    5. If FAIL → send to College
    """

    external_value = str(student.get("NPTEL External Marks", "")).strip().upper()

    # 🔴 NPTEL EXTERNAL ABSENT
    if external_value == "ABSENT":

        assignment = float(student.get("Assignment Marks", 0))
        internal_40 = custom_round((assignment / 25) * 40)

        student["Internal_Converted"] = internal_40
        student["External_Converted"] = 0

        student["Internal_Final"] = internal_40
        student["External_Final"] = "ABSENT"
        student["Total"] = internal_40

        student["Track"] = "College"
        student["Result"] = "External Absent"

        return student

    try:
        assignment = float(student.get("Assignment Marks", 0))
        external = float(student.get("NPTEL External Marks", 0))
    except:
        student["Track"] = "College"
        student["Result"] = "Invalid Marks"
        return student

    if assignment < 0 or assignment > 25:
        student["Track"] = "College"
        student["Result"] = "Invalid Assignment Marks"
        return student

    if external < 0 or external > 75:
        student["Track"] = "College"
        student["Result"] = "Invalid External Marks"
        return student

    # 🔹 Convert
    internal_40 = custom_round((assignment / 25) * 40)
    external_60 = custom_round((external / 75) * 60)

    student["Internal_Converted"] = internal_40
    student["External_Converted"] = external_60

    # 🔹 Eligibility Check
    if internal_40 < 16 or external_60 < 24:
        student["Result"] = "FAIL"
        student["Track"] = "College"
        return student

    # 🔹 If PASS → Combine & Re-divide
    combined_total = internal_40 + external_60

    final_internal = custom_round(combined_total * 0.4)
    final_external = custom_round(combined_total * 0.6)

    student["Internal_Final"] = final_internal
    student["External_Final"] = final_external
    student["Total"] = final_internal + final_external
    student["Result"] = "PASS"
    student["Track"] = "NPTEL"

    return student