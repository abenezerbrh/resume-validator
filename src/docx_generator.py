import copy
import difflib
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run


BULLET_MARKERS = ("- ", "• ", "* ", "– ")

SKILLS_HEADING = "technical skills"

# Tailored text can run slightly longer than the original, so paragraph
# spacing is tightened a little to keep the resume on the same page count.
SPACING_SCALE = 0.8


class DocxGenerationError(Exception):
    pass


def get_tailored_output_path(resume_path, output_dir="reports"):
    stem = Path(resume_path).stem
    return Path(output_dir) / f"{stem}_tailored.docx"


def _is_bullet(paragraph):
    pPr = paragraph._p.pPr
    return pPr is not None and pPr.numPr is not None


def _split_bullet_marker(line):
    for marker in BULLET_MARKERS:
        if line.startswith(marker):
            return line[len(marker):].strip(), True

    return line, False


def _find_template(line, is_marked_bullet, templates):
    # An unchanged line keeps the original paragraph exactly
    # (including hyperlinks and mixed formatting).
    for template in templates:
        if template.text.strip() == line:
            return template, True

    has_tab = "\t" in line

    candidates = [
        template for template in templates
        if ("\t" in template.text) == has_tab
    ] or templates

    def score(template):
        similarity = difflib.SequenceMatcher(
            None,
            template.text.lower(),
            line.lower()
        ).ratio()

        if is_marked_bullet and _is_bullet(template):
            similarity += 0.5

        return similarity

    return max(candidates, key=score), False


def _same_rPr(a, b):
    if a is None or b is None:
        return a is b

    return a.xml == b.xml


def _split_by_template(line, template):
    """
    Split the new line into segments that reuse the template's run
    formatting. Handles the common resume pattern of a formatted left part
    and a differently formatted part after a tab (e.g. bold company name,
    plain location) or after a label colon.
    """
    groups = [(run.text, run._r.rPr) for run in template.runs]

    if not groups:
        return [(line, None)]

    first_text, first_rPr = groups[0]

    distinct = [
        (text, rPr) for text, rPr in groups[1:]
        if text.strip() and not _same_rPr(rPr, first_rPr)
    ]

    if not distinct:
        return [(line, first_rPr)]

    second_rPr = distinct[0][1]

    if "\t" in template.text and "\t" in line:
        left, right = line.split("\t", 1)
        # Keep any spacing before the tab with the right-hand run,
        # the same way the original resume does.
        stripped_left = left.rstrip()
        return [
            (stripped_left, first_rPr),
            (left[len(stripped_left):] + "\t" + right, second_rPr)
        ]

    if first_text.rstrip().endswith(":") and ":" in line:
        label, rest = line.split(":", 1)
        return [
            (label + ":", first_rPr),
            (rest, second_rPr)
        ]

    return [(line, first_rPr)]


def _build_paragraph(line, template, parent_document):
    new_p = copy.deepcopy(template._p)

    # Keep paragraph properties (style, numbering, spacing, alignment,
    # indentation, tab stops); replace everything else.
    for child in list(new_p):
        if child.tag != qn("w:pPr"):
            new_p.remove(child)

    paragraph = Paragraph(new_p, parent_document._body)

    for text, rPr in _split_by_template(line, template):
        if not text:
            continue

        run = paragraph.add_run(text)

        if rPr is not None:
            run._r.insert(0, copy.deepcopy(rPr))

    return new_p


def _bold_label(p_element):
    """Bold the 'Label:' part of a line like 'Label: item, item'."""
    paragraph = Paragraph(p_element, None)

    if not paragraph.runs or ":" not in paragraph.runs[0].text:
        return

    first_run = paragraph.runs[0]
    label, rest = first_run.text.split(":", 1)

    if not rest:
        first_run.bold = True
        return

    label_r = copy.deepcopy(first_run._r)
    first_run._r.addprevious(label_r)

    label_run = Run(label_r, paragraph)
    label_run.text = label + ":"
    label_run.bold = True

    first_run.text = rest


def _scale_spacing(p_element, scale):
    paragraph_format = Paragraph(p_element, None).paragraph_format

    if paragraph_format.space_before:
        paragraph_format.space_before = int(
            paragraph_format.space_before * scale
        )

    if paragraph_format.space_after:
        paragraph_format.space_after = int(
            paragraph_format.space_after * scale
        )


def _same_first_rPr(a, b):
    a_runs = Paragraph(a, None).runs
    b_runs = Paragraph(b, None).runs

    if not a_runs or not b_runs:
        return False

    return _same_rPr(a_runs[0]._r.rPr, b_runs[0]._r.rPr)


def generate_tailored_docx(
    original_path,
    tailored_text,
    output_path,
    spacing_scale=SPACING_SCALE
):
    original_path = Path(original_path)
    output_path = Path(output_path)

    if not original_path.exists():
        raise DocxGenerationError(
            f"Original resume not found: {original_path}"
        )

    if output_path.resolve() == original_path.resolve():
        raise DocxGenerationError(
            "Output path is the same as the original resume. "
            "Refusing to overwrite the original."
        )

    lines = [
        line.rstrip()
        for line in tailored_text.splitlines()
        if line.strip()
    ]

    if not lines:
        raise DocxGenerationError("Tailored resume text is empty.")

    try:
        document = Document(str(original_path))
        body = document.element.body

        # Only top-level body paragraphs are rewritten. Tables and section
        # properties (page size, margins) stay exactly as in the original.
        original_paragraphs = list(document.paragraphs)
        templates = [
            paragraph for paragraph in original_paragraphs
            if paragraph.text.strip()
        ]

        if not templates:
            raise DocxGenerationError(
                "Original resume has no text paragraphs to use as a template."
            )

        new_elements = []
        skills_heading = None

        for raw_line in lines:
            line, is_marked_bullet = _split_bullet_marker(raw_line.strip())
            template, exact = _find_template(
                line,
                is_marked_bullet,
                templates
            )

            if exact:
                element = copy.deepcopy(template._p)
            else:
                element = _build_paragraph(line, template, document)

            # Track the Technical Skills section: it starts at its heading
            # and ends at the next line formatted like that heading.
            if line.lower() == SKILLS_HEADING:
                skills_heading = element
            elif skills_heading is not None:
                if _same_first_rPr(element, skills_heading):
                    skills_heading = None
                else:
                    _bold_label(element)

            _scale_spacing(element, spacing_scale)
            new_elements.append(element)

        anchor = original_paragraphs[0]._p
        for element in new_elements:
            anchor.addprevious(element)

        for paragraph in original_paragraphs:
            body.remove(paragraph._p)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(output_path))
    except DocxGenerationError:
        raise
    except Exception as error:
        raise DocxGenerationError(
            f"Could not generate tailored resume DOCX: {error}"
        ) from error

    return output_path
