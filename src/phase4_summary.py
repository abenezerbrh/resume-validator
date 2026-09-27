READY_FOR_REVIEW = "READY FOR REVIEW"
REVIEW_REQUIRED = "REVIEW REQUIRED"

BLOCKING_SEVERITIES = ("MEDIUM", "HIGH")


def get_final_status(validation_results, claims, original_unchanged=True):
    """
    Decide the Phase 4 status. `claims` is None when claim validation could
    not be completed. Missing job skills are informational and never affect
    the status.
    """
    reasons = []

    failed = sum(
        1 for result in validation_results
        if result["status"] == "FAIL"
    )

    if failed:
        reasons.append(f"{failed} resume validation check(s) failed")

    if claims is None:
        reasons.append("Claim validation could not be completed")
    else:
        blocking = sum(
            1 for claim in claims
            if claim["severity"] in BLOCKING_SEVERITIES
        )

        if blocking:
            reasons.append(
                f"{blocking} MEDIUM/HIGH potential unsupported claim(s)"
            )

    if not original_unchanged:
        reasons.append("The original resume was modified")

    return {
        "status": REVIEW_REQUIRED if reasons else READY_FOR_REVIEW,
        "reasons": reasons
    }
