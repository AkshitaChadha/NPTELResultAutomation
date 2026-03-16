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


def evaluate_result(internal, external):
    """
    Determines PASS / FAIL with reason
    """
    internal_fail = internal < 16
    external_fail = external < 24

    if not internal_fail and not external_fail:
        return "PASS"
    if internal_fail and external_fail:
        return "FAIL (Internal + External)"
    if internal_fail:
        return "FAIL (Internal)"
    if external_fail:
        return "FAIL (External)"


def calculate_final_results(student):
    """
    UNIVERSAL evaluation function that handles ALL cases:
    - NPTEL evaluation
    - College marks evaluation
    - Post-unlock re-evaluation
    """

    result = student.copy()

    status = str(result.get("Registered", "")).strip()

    # -----------------------------
    # Assignment marks handling
    # -----------------------------
    assignment_val = result.get("Assignment Marks")

    if assignment_val in ["NA", None, ""]:
        assignment = 0
    else:
        try:
            assignment = float(assignment_val)
        except:
            assignment = 0

    # Convert Assignment (25 → 40)
    internal_from_assignment = custom_round((assignment / 25) * 40) if assignment > 0 else 0
    result["Internal_Converted"] = internal_from_assignment

    college_raw = result.get("College_External_Raw")
    nptel_raw = result.get("NPTEL External Marks")

    # ------------------------------------------------
    # CASE 1 : COLLEGE EXAM ALREADY GIVEN
    # ------------------------------------------------

    if college_raw not in [None, "", "NA"] and str(college_raw).upper() != "ABSENT":

        try:
            college_external = float(college_raw)

            # Assignment internal stays same
            internal = internal_from_assignment
            external = college_external

            total = internal + external

            # Re-distribute into 40:60
            final_internal = custom_round(total * 0.4)
            final_external = custom_round(total * 0.6)

            result["Internal_Final"] = final_internal
            result["External_Final"] = final_external
            result["Total"] = total
            result["Track"] = "College Evaluated"

            result["Result"] = evaluate_result(final_internal, final_external)

            return result

        except:
            pass


    # ------------------------------------------------
    # CASE 2 : COLLEGE ABSENT
    # ------------------------------------------------

    if str(college_raw).upper() == "ABSENT":

        internal = internal_from_assignment
        total = internal

        final_internal = custom_round(total * 0.4)
        final_external = custom_round(total * 0.6)

        result["Internal_Final"] = final_internal
        result["External_Final"] = "ABSENT"
        result["College_External_Raw"] = "ABSENT"
        result["Total"] = total
        result["Track"] = "College Evaluated"
        result["Result"] = "FAIL (External)"

        return result


    # ------------------------------------------------
    # CASE 3 : NPTEL EVALUATION
    # ------------------------------------------------

    if status == "Registered" and nptel_raw not in [None, "", "NA"]:

        if str(nptel_raw).lower() == "absent":

            result["Track"] = "College"
            result["Result"] = "College Exam Required"
            result["NPTEL External Marks"] = "Absent"
            result["Internal_Final"] = internal_from_assignment
            result["External_Final"] = "NA"
            result["Total"] = internal_from_assignment

            return result

        try:

            nptel_external = float(nptel_raw)

            # Convert NPTEL 75 → 60
            external_60 = custom_round((nptel_external / 75) * 60) if nptel_external > 0 else 0

            result["External_Converted"] = external_60

            internal = internal_from_assignment
            external = external_60

            total = internal + external

            # Re-divide into 40:60
            final_internal = custom_round(total * 0.4)
            final_external = custom_round(total * 0.6)

            result["Internal_Final"] = final_internal
            result["External_Final"] = final_external
            result["Total"] = total

            result["Result"] = evaluate_result(final_internal, final_external)

            if result["Result"] == "PASS":
                result["Track"] = "NPTEL"
            else:
                result["Track"] = "College"
                result["Result"] = "College Exam Required"

            return result

        except:
            pass


    # ------------------------------------------------
    # CASE 4 : NOT ENROLLED
    # ------------------------------------------------

    if status == "Not Enrolled":

        result["Track"] = "College"
        result["Result"] = "College Exam Required"
        result["Assignment Marks"] = "NA"
        result["NPTEL External Marks"] = "NA"
        result["Internal_Final"] = 0
        result["External_Final"] = "NA"
        result["Total"] = 0

        return result


    # ------------------------------------------------
    # CASE 5 : ENROLLED
    # ------------------------------------------------

    if status == "Enrolled":

        result["Track"] = "College"
        result["Result"] = "College Exam Required"
        result["NPTEL External Marks"] = "NA"
        result["Internal_Final"] = internal_from_assignment
        result["External_Final"] = "NA"
        result["Total"] = internal_from_assignment

        return result


    # ------------------------------------------------
    # CASE 6 : REGISTERED BUT NO MARKS
    # ------------------------------------------------

    if status == "Registered":

        result["Track"] = "College"
        result["Result"] = "College Exam Required"
        result["Internal_Final"] = internal_from_assignment
        result["External_Final"] = "NA"
        result["Total"] = internal_from_assignment

        return result


    # ------------------------------------------------
    # FALLBACK
    # ------------------------------------------------

    result["Track"] = "College"
    result["Result"] = "FAIL"
    result["Internal_Final"] = 0
    result["External_Final"] = "NA"

    return result