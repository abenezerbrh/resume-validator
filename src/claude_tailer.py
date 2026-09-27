import os
import shutil
import subprocess


CLAUDE_TIMEOUT_SECONDS = 300


class ClaudeTailorError(Exception):
    pass


def build_tailor_prompt(resume_text, job_text, matched_skills, missing_skills):
    matched_lines = "\n".join(
        f"- {match['skill']} (found in resume as: {match['evidence']})"
        for match in matched_skills
    ) or "- None"

    missing_lines = "\n".join(
        f"- {skill}"
        for skill in missing_skills
    ) or "- None"

    return f"""You are helping tailor a resume to a specific job posting.

Rewrite the resume below so it is better targeted to the job posting, using ONLY information that is already present in the original resume.

STRICT RULES:
- Never invent skills, experience, projects, education, certifications, metrics, or technologies.
- Do not add a skill simply because it appears in the job posting.
- The skills listed under "MISSING FROM RESUME" must NOT be added to the resume.
- Only emphasize, reorder, or rewrite information that is supported by the original resume.
- Preserve factual accuracy: keep all names, dates, employers, schools, titles, and numbers exactly as they appear in the original resume.
- If something cannot be supported by the original resume, leave it out.

OUTPUT FORMAT:
- Return only the tailored resume as plain text.
- Keep the same section structure as the original resume.
- Do not include explanations, commentary, or notes before or after the resume.

SKILLS FROM THE JOB POSTING THAT ARE ALREADY IN THE RESUME:
{matched_lines}

MISSING FROM RESUME (do NOT add these):
{missing_lines}

=== JOB POSTING ===
{job_text}

=== ORIGINAL RESUME ===
{resume_text}
"""


def tailor_resume(resume_text, job_text, matched_skills, missing_skills):
    claude_path = shutil.which("claude")

    if claude_path is None:
        raise ClaudeTailorError(
            "Claude Code CLI not found. Install Claude Code and make sure "
            "the 'claude' command is available on your PATH."
        )

    prompt = build_tailor_prompt(
        resume_text,
        job_text,
        matched_skills,
        missing_skills
    )

    # Remove any API key so Claude Code uses the logged-in Claude subscription.
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    command = [
        claude_path,
        "-p",
        "--output-format", "text",
        "--tools", "",
        "--no-session-persistence"
    ]

    try:
        result = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=CLAUDE_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        raise ClaudeTailorError(
            f"Could not run Claude Code at: {claude_path}"
        )
    except subprocess.TimeoutExpired:
        raise ClaudeTailorError(
            f"Claude Code did not respond within "
            f"{CLAUDE_TIMEOUT_SECONDS} seconds."
        )

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise ClaudeTailorError(
            f"Claude Code returned an error (exit code {result.returncode}): "
            f"{details or 'no error details provided'}"
        )

    output = result.stdout.strip()

    if not output:
        raise ClaudeTailorError("Claude Code returned an empty response.")

    return output
