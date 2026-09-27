# Resume Validator

A Python-based tool that checks a `.docx` resume against configurable resume rules, compares it to a job posting, and uses Claude Code to produce a tailored `.docx` version of the resume.

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

## Phase 3: Resume Tailoring (in progress)

Enabled with the `--tailor` flag:

- Sends the resume text, job posting, and Phase 2 matched/missing skills to Claude through the local `claude -p` command, using your existing Claude Code login (no Anthropic API key is used)
- Instructs Claude to tailor the resume using only information already in the resume, and never to add skills from the job posting that the resume does not support
- Prints the tailored resume text in the terminal
- Generates a tailored `.docx` in `reports/`, named `<original name>_tailored.docx`
  - Reuses the original resume's formatting: page size, margins, fonts, font sizes, bold/italic, alignment, spacing, bullets, and hyperlinks on unchanged lines
  - Bolds category labels in the Technical Skills section (for example, `Software Development:`)
  - Slightly reduces paragraph spacing to help the tailored resume keep a one-page layout
- The original resume is never modified

## Tech Stack

- Python
- python-docx
- PyYAML
- pytest
- Claude Code CLI (Phase 3)

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Phase 3 also requires [Claude Code](https://claude.com/claude-code) to be installed, logged in, and available on your `PATH` as `claude`.

## Usage

Phase 1 validation and Phase 2 job analysis:

```bash
python main.py <resume.docx> <job.txt>
```

Phase 1, Phase 2, and Phase 3 tailoring:

```bash
python main.py <resume.docx> <job.txt> --tailor
```

Example:

```bash
python main.py resumes/Abenezer_Balcha_Resume_BMO.docx jobs/software_developer.txt --tailor
```

This saves the tailored resume to `reports/Abenezer_Balcha_Resume_BMO_tailored.docx`.

## Tests

```bash
pytest
```

The tests currently cover the Phase 3 DOCX generator.

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
│   ├── claude_tailer.py     # Phase 3 Claude Code integration
│   └── docx_generator.py    # Phase 3 tailored .docx generation
├── tests/
│   └── test_docx_generator.py
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
