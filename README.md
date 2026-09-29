# Resume Validator

A Python-based tool that checks a `.docx` resume against configurable resume rules, compares it to a job posting, uses Claude Code to produce a tailored `.docx` version of the resume, validates the tailored resume for human review, saves every tailored resume as a separate version, and tracks job applications in PostgreSQL.

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
- Generates a tailored `.docx`, saved as a new Phase 5 version (see below)
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

## Phase 5: Resume Versioning

Every `--tailor` run saves the tailored resume as a new version instead of overwriting an earlier one:

```text
reports/versions/<job_id>/<YYYYMMDD-HHMMSS>/
├── <original name>_tailored.docx
└── metadata.json
```

- `job_id` defaults to the job posting's file name (`jobs/software_developer.txt` → `software_developer`); use `--job-id BMO` to name it after the company. Characters that are not letters, numbers, `_`, or `-` become `_`
- `metadata.json` records the job ID, version ID, creation time, original resume file name and path, the original resume's SHA-256 hash, and the path to the tailored `.docx`
- An existing version is never overwritten: if the version folder already exists, the run stops with an error
- If `.docx` generation fails, the partly created version folder is removed
- Phase 4 validates the newly created version

## Phase 6: Application Tracker

Tracks job applications in PostgreSQL, which is the single source of truth for application data. It answers: which jobs am I tracking, what is their status, and which resume version did I use?

Each application stores: `id`, `company`, `job_title`, `job_id`, `job_posting_path`, `job_url` (optional), `date_added`, `date_applied` (optional), `status`, `resume_version_id` (optional), `resume_version_path` (optional), and `notes` (optional).

- **Statuses:** `Saved`, `Tailored`, `Applied`, `Interview`, `Offer`, `Rejected`, `Withdrawn`. Any other status is rejected, both in Python and by a database `CHECK` constraint. Status names are case-insensitive on the command line
- **One application per `job_id`:** adding a second application with the same `job_id` is refused, and the existing record is left unchanged
- **`job_id` links to Phase 5:** it must be a valid version folder name, so an application's resume versions live under `reports/versions/<job_id>/`
- **Resume versions:** the database stores only the version ID and the path to the `.docx`; the files themselves stay under Phase 5 versioning. Before linking, the tracker checks that the version belongs to the same `job_id` and that its `.docx` exists
- **Tailoring is not applying:** linking a resume version moves a `Saved` application to `Tailored` and leaves any later status (such as `Applied` or `Interview`) unchanged. An application only becomes `Applied` when you set it
- **`date_applied`** is set to today the first time an application is marked `Applied` without a date, and is kept on later status changes

All SQL lives in `src/database.py`; the tracking rules live in `src/application_tracker.py`. The `applications` table is created automatically the first time the tracker connects.

## Tech Stack

- Python
- python-docx
- PyYAML
- pytest
- Claude Code CLI (Phases 3 and 4)
- PostgreSQL, psycopg, and python-dotenv (Phase 6)

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Phases 3 and 4 also require [Claude Code](https://claude.com/claude-code) to be installed, logged in, and available on your `PATH` as `claude`.

### PostgreSQL (Phase 6)

The application tracker needs a running PostgreSQL server. Phases 1–5 do not.

1. Copy `.env.example` to `.env` and fill in your settings. `.env` is git-ignored; never commit it.

   ```text
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=resume_validator
   DB_USER=postgres
   DB_PASSWORD=<your password>
   TEST_DB_NAME=resume_validator_test
   ```

   Variables already set in your environment take priority over `.env`.

2. Create the application database once (the `applications` table is created automatically):

   ```bash
   "C:\Program Files\PostgreSQL\18\bin\createdb.exe" -U postgres resume_validator
   ```

The test database (`TEST_DB_NAME`) is created automatically by the tests.

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
python main.py resumes/Abenezer_Balcha_Resume_BMO.docx jobs/software_developer.txt --tailor --job-id BMO
```

This saves the tailored resume to a new version such as `reports/versions/BMO/20260929-150218/Abenezer_Balcha_Resume_BMO_tailored.docx` and prints the Phase 4 validation results.

### Tracking applications

Add an application (starts as `Saved`):

```bash
python tracker.py add --company BMO --job-title "Software Developer Intern" --job-id BMO --job-posting jobs/software_developer.txt
```

Optional: `--job-url <url>`, `--notes <text>`, `--status <status>`.

Tailor the resume and link the new version to the application:

```bash
python main.py resumes/Abenezer_Balcha_Resume_BMO.docx jobs/software_developer.txt --tailor --job-id BMO --track
```

`--track` checks that the application exists before calling Claude, then links the new version after it is saved. Without `--track`, the database is not used.

Other commands:

```bash
python tracker.py list                              # all applications
python tracker.py show BMO                          # one application
python tracker.py status BMO Applied                # update status (date_applied defaults to today)
python tracker.py status BMO Applied --date-applied 2026-09-29
python tracker.py link BMO reports/versions/BMO/20260929-150218   # link an existing version
```

## Tests

```bash
pytest
```

The tests cover the Phase 3 DOCX generator, Phase 4 validation (resume validation, job alignment, claim validation parsing, and final status), Phase 5 versioning, and the Phase 6 application tracker. Claude calls are mocked, so the tests do not need Claude Code.

The Phase 6 tests use only the database named by `TEST_DB_NAME`, never `DB_NAME`, and clear its `applications` table before each test. They are skipped if `TEST_DB_NAME` is not set, and fail if it is the same as `DB_NAME`.

## Project Structure

```text
resume-validator/
├── config/
│   └── rules.yaml           # Resume rules and job skill aliases
├── jobs/
│   └── software_developer.txt
├── resumes/                 # Input resumes (.docx files are git-ignored)
├── reports/                 # Generated output (git-ignored)
│   └── versions/            # Phase 5 tailored resume versions
├── src/
│   ├── parser.py            # Reads the resume and job posting
│   ├── rules.py             # Section and contact checks
│   ├── validator.py         # Runs the Phase 1 checks
│   ├── job_analyzer.py      # Phase 2 skill matching
│   ├── claude_tailer.py     # Claude Code integration and Phase 3 tailoring
│   ├── docx_generator.py    # Phase 3 tailored .docx generation
│   ├── claim_validator.py   # Phase 4 unsupported-claim detection
│   ├── phase4_summary.py    # Phase 4 final status
│   ├── resume_versioning.py # Phase 5 tailored resume versions
│   ├── database.py          # Phase 6 PostgreSQL access (all SQL)
│   └── application_tracker.py # Phase 6 application tracking rules
├── tests/
│   ├── test_docx_generator.py
│   ├── test_validator.py
│   ├── test_phase4.py
│   ├── test_resume_versioning.py
│   └── test_application_tracker.py
├── .env.example             # Database settings template (copy to .env)
├── main.py
├── tracker.py               # Phase 6 application tracker command line
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
- Only one application can be tracked per `job_id`; applying to the same job again needs a different `job_id` (for example, `BMO-2027`)
- Application statuses can change in any order; only unknown statuses are rejected
- Resume version paths are stored relative to the project folder, so they only resolve from this project
- The `applications` table is created if missing, but later schema changes will need a migration step
