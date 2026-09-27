# Resume Validator

A Python-based tool that checks a `.docx` resume against configurable resume rules, compares it to a job posting, uses Claude Code to produce a tailored `.docx` version of the resume, and validates the tailored resume for human review.

## Phase 1: Resume Validation

Validates a `.docx` resume against the rules in `config/rules.yaml`:

- Required resume sections
- Contact information
  - Email
  - Phone number
  - LinkedIn
  - GitHub
- Configurable resume rules
- Terminal validation report

## Phase 2: Job Analysis

Compares the resume to a plain-text job posting:

- Finds skills in the job posting using the skill list and aliases in `config/rules.yaml` (for example, `React`, `React.js`, `ReactJS`)
- Lists which of those skills are found in the resume, and the matching term
- Lists which of those skills are missing from the resume
- Prints the results in a terminal job analysis report

## Phase 3: Resume Tailoring

Enabled with the `--tailor` flag:

- Sends the resume text, job posting, and Phase 2 matched/missing skills to Claude through the local `claude -p` command, using your existing Claude Code login (no Anthropic API key is used)
- Instructs Claude to tailor the resume using only information already in the resume, and never to add skills from the job posting that the resume does not support
- Prints the tailored resume text in the terminal
- Generates a tailored `.docx` in `reports/`, named `<original name>_tailored.docx`
  - Reuses the original resume's formatting: page size, margins, fonts, font sizes, bold/italic, alignment, spacing, bullets, and hyperlinks on unchanged lines
  - Bolds category labels in the Technical Skills section (for example, `Software Development:`)
  - Slightly reduces paragraph spacing to help the tailored resume keep a one-page layout
- The original resume is never modified

## Phase 4: Tailored Resume Validation

Runs automatically with `--tailor`, after the tailored `.docx` is generated. The generated `.docx` is the source of truth for these checks.

- **Resume validation:** runs the Phase 1 checks against the tailored `.docx`
- **Job alignment:** runs the Phase 2 skill comparison against the tailored `.docx`, using the same job posting and skill aliases, and lists matched and still-missing skills
- **Unsupported-claim detection:** asks Claude (through the same local `claude -p` integration) to compare the original and tailored resumes and flag factual claims the original resume does not support, such as new skills, technologies, metrics, or strengthened responsibilities
  - The job posting is given as context only and is never treated as evidence of the candidate's experience
  - Each flagged claim includes the claim, why it may not be supported, the closest original resume evidence, and a severity (`LOW`, `MEDIUM`, or `HIGH`)
  - Findings are for human review only; the resume is not changed or regenerated
- **Final result:** prints a combined summary and a final status
  - `⚠ REVIEW REQUIRED` if any resume validation check fails, any `MEDIUM` or `HIGH` claim is flagged, claim validation could not be completed, or the original resume was modified
  - `✓ READY FOR REVIEW` otherwise
  - Missing job skills are informational and do not affect the status
  - The status is a recommendation for human review, not an automatic rejection
- Confirms the original resume is unchanged by comparing its SHA-256 hash before and after the run

## Tech Stack

- Python
- python-docx
- PyYAML
- pytest
- Claude Code CLI (Phases 3 and 4)

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Phases 3 and 4 also require [Claude Code](https://claude.com/claude-code) to be installed, logged in, and available on your `PATH` as `claude`.

## Usage

Phase 1 validation and Phase 2 job analysis:

```bash
python main.py <resume.docx> <job.txt>
```

Phase 1, Phase 2, Phase 3 tailoring, and Phase 4 validation:

```bash
python main.py <resume.docx> <job.txt> --tailor
```

Example:

```bash
python main.py resumes/Abenezer_Balcha_Resume_BMO.docx jobs/software_developer.txt --tailor
```

This saves the tailored resume to `reports/Abenezer_Balcha_Resume_BMO_tailored.docx` and prints the Phase 4 validation results.

## Tests

```bash
pytest
```

The tests cover the Phase 3 DOCX generator and Phase 4 validation (resume validation, job alignment, claim validation parsing, and final status). Claude calls are mocked, so the tests do not need Claude Code.

## Project Structure

```text
resume-validator/
├── config/
│   └── rules.yaml           # Resume rules and job skill aliases
├── jobs/
│   └── software_developer.txt
├── resumes/                 # Input resumes (.docx files are git-ignored)
├── reports/                 # Generated tailored resumes (git-ignored)
├── src/
│   ├── parser.py            # Reads the resume and job posting
│   ├── rules.py             # Section and contact checks
│   ├── validator.py         # Runs the Phase 1 checks
│   ├── job_analyzer.py      # Phase 2 skill matching
│   ├── claude_tailer.py     # Claude Code integration and Phase 3 tailoring
│   ├── docx_generator.py    # Phase 3 tailored .docx generation
│   ├── claim_validator.py   # Phase 4 unsupported-claim detection
│   └── phase4_summary.py    # Phase 4 final status
├── tests/
│   ├── test_docx_generator.py
│   ├── test_validator.py
│   └── test_phase4.py
├── main.py
├── pytest.ini
├── requirements.txt
└── README.md
```

## Current Limitations

- Skill and section matching uses plain text search, so short terms can match inside longer words (for example, `Git` inside `GitHub`)
- Text inside resume tables is not read
- Claude's tailored output varies between runs, and the one-page spacing adjustment is fixed, so a much longer tailored resume could still extend past one page
- If Claude rewrites the header line, its hyperlinks are not kept
- Claim validation results come from Claude and can vary between runs
- The program exits successfully even when the final status is `REVIEW REQUIRED`; the status is reported, not enforced
