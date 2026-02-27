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

    # 🔹 Registration check
    registered = str(student.get("Registered", "")).strip().lower()

    if registered != "registered":
        student["Track"] = "College"
        student["Result"] = "College Exam Required"
        return student

    # 🔹 Extract marks safely
    try:
        assignment = float(student.get("Assignment Marks", 0))
        external = float(student.get("NPTEL External Marks", 0))
    except:
        student["Track"] = "College"
        student["Result"] = "Invalid Marks"
        return student

    # 🔹 Validate ranges
    if assignment < 0 or assignment > 25:
        student["Track"] = "College"
        student["Result"] = "Invalid Assignment Marks"
        return student

    if external < 0 or external > 75:
        student["Track"] = "College"
        student["Result"] = "Invalid External Marks"
        return student

    # 🔹 Convert Assignment (25 → 40)
    internal_40 = custom_round((assignment / 25) * 40)

    # 🔹 Convert External (75 → 60)
    external_60 = custom_round((external / 75) * 60)

    student["Internal_Converted"] = internal_40
    student["External_Converted"] = external_60

    # 🔹 PASS CHECK (Direct Model)
    if internal_40 >= 16 and external_60 >= 24:

        student["Internal_Final"] = internal_40
        student["External_Final"] = external_60
        student["Total"] = internal_40 + external_60

        student["Track"] = "NPTEL"
        student["Result"] = "PASS"
        return student

    # 🔹 Otherwise → College Route
    student["Track"] = "College"
    student["Result"] = "College Exam Required"
    return student