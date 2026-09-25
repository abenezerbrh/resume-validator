def extract_job_skills(job_text, skills):
    job_text_lower = job_text.lower()

    found_skills = {}

    for skill, aliases in skills.items():
        for alias in aliases:
            if alias.lower() in job_text_lower:
                found_skills[skill] = aliases
                break

    return found_skills


def compare_resume_to_job(resume_text, job_skills):
    resume_text_lower = resume_text.lower()

    matched_skills = []
    missing_skills = []

    for skill, aliases in job_skills.items():
        matched_alias = None

        for alias in aliases:
            if alias.lower() in resume_text_lower:
                matched_alias = alias
                break

        if matched_alias:
            matched_skills.append({
                "skill": skill,
                "evidence": matched_alias
            })
        else:
            missing_skills.append(skill)

    return {
        "matched_skills": matched_skills,
        "missing_skills": missing_skills
    }