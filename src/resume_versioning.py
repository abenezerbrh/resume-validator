import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from src.docx_generator import generate_tailored_docx


VERSIONS_DIR = Path("reports") / "versions"

METADATA_FILENAME = "metadata.json"


class VersionExistsError(Exception):
    pass


def make_job_id(text):
    # Folder-safe identifier, e.g. "RBC / Dev Role" -> "RBC_Dev_Role".
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(text)).strip("_") or "job"


def job_id_from_path(job_path):
    # e.g. "jobs/software_developer.txt" -> "software_developer"
    return make_job_id(Path(job_path).stem)


def make_version_id(created_at):
    return created_at.strftime("%Y%m%d-%H%M%S")


def get_version_dir(job_id, version_id, output_dir=VERSIONS_DIR):
    return Path(output_dir) / job_id / version_id


def get_version_docx_path(resume_path, version_dir):
    return Path(version_dir) / f"{Path(resume_path).stem}_tailored.docx"


def _file_sha256(file_path):
    with open(file_path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def save_tailored_version(
    resume_path,
    tailored_text,
    job_id,
    created_at=None,
    output_dir=VERSIONS_DIR,
    generate=generate_tailored_docx
):
    """
    Save a tailored resume as a new version:

        <output_dir>/<job_id>/<version_id>/<resume stem>_tailored.docx
        <output_dir>/<job_id>/<version_id>/metadata.json

    Raises VersionExistsError instead of overwriting an existing version.
    """
    created_at = created_at or datetime.now()
    version_id = make_version_id(created_at)
    version_dir = get_version_dir(job_id, version_id, output_dir)

    version_dir.parent.mkdir(parents=True, exist_ok=True)

    # Creating the directory is the overwrite guard: it fails if the
    # version already exists, so nothing in it is ever replaced.
    try:
        version_dir.mkdir()
    except FileExistsError:
        raise VersionExistsError(
            f"Tailored resume version already exists: {version_dir}"
        ) from None

    docx_path = get_version_docx_path(resume_path, version_dir)

    try:
        generate(resume_path, tailored_text, docx_path)
    except Exception:
        # Only remove the directory this call just created.
        shutil.rmtree(version_dir, ignore_errors=True)
        raise

    metadata = {
        "job_id": job_id,
        "version_id": version_id,
        "created_at": created_at.isoformat(timespec="seconds"),
        "original_resume": Path(resume_path).name,
        "original_resume_path": str(resume_path),
        "original_resume_sha256": _file_sha256(resume_path),
        "tailored_docx": str(docx_path),
    }

    (version_dir / METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8"
    )

    return metadata
