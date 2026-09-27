import json

from src.claude_tailer import run_claude, ClaudeTailorError


SEVERITIES = ("LOW", "MEDIUM", "HIGH")


class ClaimValidationError(Exception):
    pass


def build_claim_prompt(original_text, tailored_text, job_text):
    return f"""You are reviewing a tailored resume for factual accuracy.

Compare the TAILORED RESUME against the ORIGINAL RESUME and identify factual claims in the tailored resume that are NOT supported by the original resume.

A claim is unsupported if it introduces or strengthens facts about any of:
skills, technologies, tools, responsibilities, employers, job titles, education, certifications, projects, metrics, numbers, dates, achievements, or technical experience.

RULES:
- The ORIGINAL RESUME is the only evidence of what the candidate has done.
- The JOB POSTING is context only. It may explain why wording was chosen, but it is NEVER evidence that the candidate has a skill or experience.
- Compare meaning, not individual words.
- Be conservative. Do NOT flag a statement that is a reasonable rewrite of something clearly present in the original resume.
- DO flag new details added to an otherwise supported statement (for example, a new technology, a new type of collaborator, a larger scope, or a new metric).

EXAMPLES:
- Original "Designed REST API integrations." -> Tailored "Designed and integrated REST API endpoints."
  NOT flagged: it preserves the original meaning.
- Original "Designed REST API integrations." -> Tailored "Designed and deployed REST APIs using AWS."
  FLAGGED: AWS and deployment experience are not supported by the original resume.
- Original "Collaborated with cross-functional stakeholders." -> Tailored "Collaborated with developers and cross-functional stakeholders."
  FLAGGED: collaboration with developers is not established by the original resume.

SEVERITY:
- HIGH: a new skill, technology, tool, employer, job title, degree, certification, project, metric, number, or date that does not appear in the original resume.
- MEDIUM: an existing item whose scope, responsibility, ownership, or impact is strengthened beyond what the original resume states.
- LOW: minor wording that could be read as slightly stronger than the original, but is mostly supported.

OUTPUT FORMAT:
Return ONLY a JSON object, with no other text and no code fences, in exactly this shape:
{{"claims": [{{"claim": "<exact text from the tailored resume>", "reason": "<why it may not be supported>", "original_evidence": "<closest related text from the original resume, or empty string if none>", "severity": "LOW|MEDIUM|HIGH"}}]}}

If there are no unsupported claims, return: {{"claims": []}}

=== JOB POSTING (context only, NOT evidence) ===
{job_text}

=== ORIGINAL RESUME ===
{original_text}

=== TAILORED RESUME ===
{tailored_text}
"""


def parse_claims_response(response):
    text = response.strip()
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ClaimValidationError(
            "Claude's claim validation response did not contain JSON."
        )

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as error:
        raise ClaimValidationError(
            f"Claude's claim validation response was not valid JSON: {error}"
        ) from error

    raw_claims = data.get("claims") if isinstance(data, dict) else None

    if not isinstance(raw_claims, list):
        raise ClaimValidationError(
            "Claude's claim validation response is missing a 'claims' list."
        )

    claims = []

    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict) or not raw_claim.get("claim"):
            raise ClaimValidationError(
                f"Invalid claim in Claude's response: {raw_claim!r}"
            )

        severity = str(raw_claim.get("severity", "")).strip().upper()

        if severity not in SEVERITIES:
            raise ClaimValidationError(
                f"Invalid severity {raw_claim.get('severity')!r} "
                f"for claim: {raw_claim['claim']!r}"
            )

        claims.append({
            "claim": str(raw_claim["claim"]).strip(),
            "reason": str(raw_claim.get("reason") or "").strip(),
            "original_evidence": str(
                raw_claim.get("original_evidence") or ""
            ).strip(),
            "severity": severity
        })

    return claims


def validate_claims(original_text, tailored_text, job_text, run=run_claude):
    prompt = build_claim_prompt(original_text, tailored_text, job_text)

    try:
        response = run(prompt)
    except ClaudeTailorError as error:
        raise ClaimValidationError(str(error)) from error

    return parse_claims_response(response)
