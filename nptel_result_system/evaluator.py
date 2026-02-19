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

    # Validate limits
    if assignment < 0 or assignment > 25:
        student["Track"] = "College"
        student["Result"] = "Invalid Assignment Marks"
        return student

    if external < 0 or external > 75:
        student["Track"] = "College"
        student["Result"] = "Invalid External Marks"
        return student

    # ===============================
    # 🔹 STEP 1 – Convert to 40–60
    # ===============================
    internal_40 = custom_round((assignment / 25) * 40)
    external_60 = custom_round((external / 75) * 60)

    combined_total = internal_40 + external_60  # out of 100

    # ===============================
    # 🔹 STEP 2 – Re-divide 100 into 40–60
    # ===============================
    final_internal = custom_round(combined_total * 0.4)
    final_external = custom_round(combined_total * 0.6)

    total = final_internal + final_external

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

    student["Track"] = "NPTEL"

    return student
