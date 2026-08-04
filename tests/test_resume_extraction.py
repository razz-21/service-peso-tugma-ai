import fitz
from httpx import AsyncClient

from app.api.v1.routes.applicants.applicants_extraction import parse_resume
from app.core.config import settings
from app.matching.extraction import ExtractMeta, extract_text

_SAMPLE_RESUME = """JUAN DELA CRUZ
123 Rizal Street, Barangay Uno, Quezon City
juan.delacruz@example.com | +63 917 123 4567

SKILLS
Python, FastAPI, MongoDB, Docker, Git

WORK EXPERIENCE
Software Engineer, Acme Corp    Jan 2020 - Present
Junior Developer, Beta Inc      2018 - 2020

EDUCATION
Bachelor of Science in Computer Science
University of the Philippines
2018

TRAININGS
Agile Fundamentals Seminar
AWS Cloud Practitioner Workshop

ELIGIBILITY
Civil Service Professional Eligibility
"""


def _meta(text: str) -> ExtractMeta:
    return ExtractMeta(method="text", ocr_used=False, pages=1, char_count=len(text))


def test_parse_resume_extracts_core_fields() -> None:
    result = parse_resume(_SAMPLE_RESUME, _meta(_SAMPLE_RESUME))

    # Name (uppercase in source — casing is the officer's to fix).
    assert result.firstname == "JUAN"
    assert result.middlename == "DELA"
    assert result.lastname == "CRUZ"

    # Contact.
    assert result.email_address == "juan.delacruz@example.com"
    assert result.primary_mobile_number == "09171234567"  # +63 ... normalized

    # Skills.
    assert "Python" in result.technical_skills
    assert len(result.technical_skills) == 5

    # Education.
    assert result.educational_background is not None
    assert "Bachelor" in (result.educational_background.highest_education_level or "")
    assert "University" in (result.educational_background.school_university or "")
    assert result.educational_background.year_graduated == "2018"

    # Work experience (two entries; dates parsed to ISO, "Present" -> ongoing).
    assert len(result.work_experience) == 2
    first = result.work_experience[0]
    assert first.position is not None and first.position.startswith("Software Engineer")
    assert first.start_date == "2020-01-01"
    assert first.end_date is None

    # Trainings + eligibility.
    assert len(result.trainings) == 2
    assert len(result.eligibility) == 1

    # Meta is carried through and raw text is preserved for embedding.
    assert result.meta.method == "text"
    assert result.raw_text == _SAMPLE_RESUME


def test_parse_resume_normalizes_mobile_formats() -> None:
    for raw, expected in (
        ("Contact: 0917-123-4567", "09171234567"),
        ("Mobile: +63 918 765 4321", "09187654321"),
        ("Cell no. 09195551234", "09195551234"),
    ):
        result = parse_resume(raw, _meta(raw))
        assert result.primary_mobile_number == expected


def test_parse_resume_reads_labeled_personal_fields() -> None:
    text = "Sex: Female\nDate of Birth: January 5, 1990\n"
    result = parse_resume(text, _meta(text))
    assert result.sex == "Female"
    # dateparser normalizes the free-form DOB to ISO.
    assert result.date_of_birth == "1990-01-05"


def test_parse_resume_parses_various_work_dates() -> None:
    text = (
        "WORK EXPERIENCE\n"
        "Software Engineer, Acme  June 2018 - March 2020\n"
        "Analyst, Beta  06/2017 - 12/2019\n"
        "Intern, Gamma  2015 - Present\n"
    )
    work = parse_resume(text, _meta(text)).work_experience
    assert len(work) == 3
    assert (work[0].start_date, work[0].end_date) == ("2018-06-01", "2020-03-01")
    assert (work[1].start_date, work[1].end_date) == ("2017-06-01", "2019-12-01")
    assert (work[2].start_date, work[2].end_date) == ("2015-01-01", None)


def test_parse_resume_ignores_wrapped_description_lines() -> None:
    # A job header (with dates) followed by several wrapped description lines must
    # yield ONE entry, not one per line. Previously every non-date line under
    # WORK EXPERIENCE became its own bogus "position".
    text = (
        "WORK EXPERIENCE\n"
        "IT Faculty, Liceo de Cagayan University  Jul 2025 - Present\n"
        "Delivered instruction in core IT subjects, including Object-Oriented\n"
        "Programming, Platform Technologies, and Living in the IT Era, focusing on\n"
        "practical and industry-relevant skills. Also served as a training coordinator.\n"
        "Front-end Engineer, ORQ.ai  Apr 2024 - May 2025\n"
        "Worked on developing and implementing front-end features and application\n"
        "workflows, ensuring alignment with product requirements and user goals.\n"
    )
    work = parse_resume(text, _meta(text)).work_experience
    assert len(work) == 2
    assert work[0].position == "IT Faculty, Liceo de Cagayan University"
    assert (work[0].start_date, work[0].end_date) == ("2025-07-01", None)
    assert work[1].position == "Front-end Engineer, ORQ.ai"
    assert (work[1].start_date, work[1].end_date) == ("2024-04-01", "2025-05-01")


def test_parse_resume_titles_a_date_only_header_from_prior_line() -> None:
    # Resume that puts the role above a date-only line: the entry borrows the
    # preceding line as its position rather than leaving it empty.
    text = "WORK EXPERIENCE\nSoftware Engineer, Acme\nJan 2020 - Dec 2022\n"
    work = parse_resume(text, _meta(text)).work_experience
    assert len(work) == 1
    assert work[0].position == "Software Engineer, Acme"
    assert (work[0].start_date, work[0].end_date) == ("2020-01-01", "2022-12-01")


def test_parse_resume_stops_skills_at_projects_section() -> None:
    # A PROJECTS section after SKILLS must not bleed project names, prose, and
    # links into technical_skills (the reported bug).
    text = (
        "SKILLS\n"
        "HTML5\n"
        "TypeScript\n"
        "Material UI\n"
        "Unit Testing\n"
        "Hono-API\n"
        "Playwright\n"
        "PROJECTS\n"
        "HSI ATTENDANCE - ANDROID\n"
        "log for their work. Allows employees to clock-in/clock-out\n"
        "Tech Stack: Android, Kotlin, AndroidStudio\n"
        "Link: https://play.google.com/store/apps/details?id=com.r.hsiattendance\n"
        "POLLIFY - WEB\n"
        "A web-based application designed to facilitate the creation of polls.\n"
    )
    skills = parse_resume(text, _meta(text)).technical_skills
    assert skills == [
        "HTML5",
        "TypeScript",
        "Material UI",
        "Unit Testing",
        "Hono-API",
        "Playwright",
    ]


def test_parse_skills_drops_links_and_prose_in_a_shared_block() -> None:
    # Even without a section break, links / labels / sentences are filtered while
    # legitimate skills (incl. dotted names) survive.
    text = (
        "SKILLS\n"
        "Node.js, .NET, Socket.io\n"
        "scan QR for attendance. Then update information\n"
        "Link: https://example.com/x, id=com.foo.bar, Tech Stack: Android\n"
        "etc..\n"
    )
    skills = parse_resume(text, _meta(text)).technical_skills
    assert skills == ["Node.js", ".NET", "Socket.io"]


def test_extract_text_reads_two_columns_in_order() -> None:
    # Build a two-column page where each line is its own block; a naive
    # top-to-bottom-by-line read would interleave the columns.
    doc = fitz.open()
    page = doc.new_page()
    for y, text in ((120, "EDUCATION"), (150, "Bachelor of Science"), (180, "University of Test")):
        page.insert_text((60, y), text)
    for y, text in ((120, "SKILLS"), (150, "Python Django"), (180, "Docker Kubernetes")):
        page.insert_text((330, y), text)
    pdf_bytes = doc.tobytes()
    doc.close()

    text, meta = extract_text(pdf_bytes)
    assert meta.method == "text"
    # The whole left column is read before the right column (no interleaving).
    assert text.index("University of Test") < text.index("SKILLS")
    assert text.index("EDUCATION") < text.index("Bachelor of Science")


def test_parse_resume_empty_is_safe() -> None:
    result = parse_resume("", _meta(""))
    assert result.firstname is None
    assert result.technical_skills == []
    assert result.work_experience == []
    assert result.educational_background is None
    assert result.meta.pages == 1
    assert result.raw_text == ""


async def test_extract_endpoint_requires_auth(client: AsyncClient) -> None:
    # The slice is mounted with `auth_required`, so the upload is rejected before
    # any parsing happens.
    response = await client.post(f"{settings.API_V1_PREFIX}/applicants/extract")
    assert response.status_code == 401


async def test_upload_file_endpoint_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        f"{settings.API_V1_PREFIX}/applicants/{'0' * 8}-0000-0000-0000-000000000000/files"
    )
    assert response.status_code == 401
