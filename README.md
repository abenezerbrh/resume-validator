# Resume Validator

A Python-based resume validation tool that checks a `.docx` resume against configurable resume rules.

## Phase 1

The first version focuses on basic resume validation, including:

- Required resume sections
- Contact information
- Email
- Phone number
- LinkedIn
- GitHub
- Configurable resume rules
- Terminal validation report

## Tech Stack

- Python
- python-docx
- PyYAML
- pytest

## Project Structure

```text
resume-validator/
├── config/
│   └── rules.yaml
├── resumes/
├── reports/
├── src/
│   ├── parser.py
│   ├── rules.py
│   └── validator.py
├── tests/
├── main.py
├── requirements.txt
└── README.md