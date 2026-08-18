"""Rule-based resume field parser (paper Figure 8, §6.3 of the plan).

Turns the raw text produced by ``app.matching.extraction.extract_text`` into a
best-effort ``ResumeExtraction`` — contact fields via regex, then section
segmentation (education / work / skills / trainings / eligibility) with light
per-section heuristics. Deterministic and dependency-free, so it is unit-testable
against text fixtures. Accuracy on free-form resumes is moderate by design; the
officer reviews and corrects every field before submit (Human-in-the-Loop).
"""

import re

import dateparser

from app.matching.extraction import ExtractMeta

from .applicants_models import (
    EducationalBackground,
    Eligibility,
    Training,
    WorkExperience,
)
from .applicants_schemas import ResumeExtraction, ResumeExtractionMeta

# --- Caps (keep prefill sane; the officer adds anything beyond these) ------
_MAX_SKILLS = 40
_MAX_WORK = 15
_MAX_TRAININGS = 25
_MAX_ELIGIBILITY = 15
_MAX_ITEM_LEN = 120

# --- Contact regexes -------------------------------------------------------
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Loose candidate for a phone number; digits are extracted and normalized after.
_PHONE_CANDIDATE = re.compile(r"[+(]?\d[\d\s\-().]{8,}\d")
_SEX = re.compile(r"\bsex\b\s*[:\-]?\s*(male|female)\b", re.IGNORECASE)
_DOB = re.compile(r"date of birth\s*[:\-]?\s*([A-Za-z0-9,./\- ]{6,20})", re.IGNORECASE)

# --- Section headers -> canonical key --------------------------------------
_HEADER_LOOKUP: dict[str, str] = {}
for _canonical, _labels in {
    "education": ("EDUCATION", "EDUCATIONAL BACKGROUND", "ACADEMIC BACKGROUND"),
    "work": (
        "WORK EXPERIENCE",
        "EMPLOYMENT HISTORY",
        "WORK HISTORY",
        "PROFESSIONAL EXPERIENCE",
        "EXPERIENCE",
    ),
    "skills": ("SKILLS", "TECHNICAL SKILLS", "KEY SKILLS", "CORE COMPETENCIES", "COMPETENCIES"),
    "trainings": (
        "TRAININGS",
        "TRAINING",
        "SEMINARS",
        "TRAININGS AND SEMINARS",
        "SEMINARS AND TRAININGS",
        "PROFESSIONAL DEVELOPMENT",
    ),
    "eligibility": (
        "ELIGIBILITY",
        "ELIGIBILITIES",
        "CERTIFICATIONS",
        "CERTIFICATES",
        "LICENSES",
        "CERTIFICATIONS AND LICENSES",
    ),
}.items():
    for _label in _labels:
        _HEADER_LOOKUP[_label] = _canonical

# Non-target sections recognized ONLY as boundaries, so their content (project
# blurbs, links, prose, references) can't bleed into the preceding target
# section — e.g. a "PROJECTS" block leaking into SKILLS. Mapped to a sentinel key
# that ``parse_resume`` never reads back, so the lines are effectively dropped.
_IGNORED_SECTION = "_ignored"
for _label in (
    "PROJECTS",
    "PROJECT",
    "PERSONAL PROJECTS",
    "KEY PROJECTS",
    "ACADEMIC PROJECTS",
    "AWARDS",
    "AWARDS AND RECOGNITION",
    "HONORS",
    "ACHIEVEMENTS",
    "REFERENCES",
    "CHARACTER REFERENCES",
    "LANGUAGES",
    "INTERESTS",
    "HOBBIES",
    "AFFILIATIONS",
    "ORGANIZATIONS",
    "MEMBERSHIPS",
    "SUMMARY",
    "PROFESSIONAL SUMMARY",
    "OBJECTIVE",
    "CAREER OBJECTIVE",
    "PORTFOLIO",
    "PUBLICATIONS",
    "VOLUNTEER EXPERIENCE",
):
    _HEADER_LOOKUP.setdefault(_label, _IGNORED_SECTION)

_ADDRESS_KEYWORDS = (
    "street",
    "st.",
    "brgy",
    "barangay",
    "purok",
    "subdivision",
    "city",
    "province",
    "phase",
    "block",
    "lot",
    "zone",
)
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
_EDUCATION_KEYWORDS = (
    "doctor",
    "phd",
    "doctorate",
    "master",
    "postgraduate",
    "bachelor",
    "college",
    "baccalaureate",
    "associate",
    "vocational",
    "diploma",
    "tesda",
    "senior high",
    "high school",
    "secondary",
    "elementary",
    "primary",
)
_SCHOOL_KEYWORDS = ("university", "college", "institute", "polytechnic", "school", "academy")
_COURSE_KEYWORDS = (
    "bachelor of",
    "master of",
    "bs ",
    "ba ",
    "diploma in",
    "major in",
    "associate in",
)
# Signals that a date token carries a month (a month name, or a numeric date like
# "06/2018"), so a bare year isn't sent through dateparser (which would otherwise
# fill in the current month).
_HAS_MONTH = re.compile(
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2}\s*[/.\-]\s*\d",
    re.IGNORECASE,
)
# A single date: an optional numeric "MM/" or month-name prefix, then a 4-digit
# year — covers "2018", "June 2018", and "06/2018". dateparser resolves the month.
_DATE_TOKEN = r"(?:\d{1,2}\s*[/.\-]\s*)?(?:[A-Za-z]{3,9}\.?\s*)?\d{4}"
_DATE_RANGE = re.compile(
    rf"(?P<start>{_DATE_TOKEN})\s*(?:-|–|—|to|until)\s*" rf"(?P<end>present|current|{_DATE_TOKEN})",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def parse_resume(raw_text: str, meta: ExtractMeta) -> ResumeExtraction:
    """Parse resume text into best-effort structured applicant fields."""
    result_meta = ResumeExtractionMeta(
        method=meta.method,
        ocr_used=meta.ocr_used,
        pages=meta.pages,
        char_count=meta.char_count,
    )
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    top, sections = _segment(lines)

    firstname, middlename, lastname, suffix = _parse_name(top)
    education = _parse_education(sections.get("education", []))

    return ResumeExtraction(
        firstname=firstname,
        middlename=middlename,
        lastname=lastname,
        suffix=suffix,
        date_of_birth=_parse_dob(raw_text),
        sex=_parse_sex(raw_text),
        email_address=_first_match(_EMAIL, raw_text),
        primary_mobile_number=_find_mobile(raw_text),
        present_address=None,
        educational_background=education,
        work_experience=_parse_work(sections.get("work", [])),
        trainings=_parse_trainings(sections.get("trainings", [])),
        eligibility=_parse_eligibility(sections.get("eligibility", [])),
        technical_skills=_parse_skills(sections.get("skills", [])),
        raw_text=raw_text,
        meta=result_meta,
    )


# --- Sectioning ------------------------------------------------------------
def _normalize_header(line: str) -> str:
    return re.sub(r"[^A-Z0-9 &]", "", line.upper()).strip()


def _segment(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Split lines into the top block (before any header) and named sections."""
    top: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        normalized = _normalize_header(line)
        canonical = _HEADER_LOOKUP.get(normalized) if len(normalized.split()) <= 4 else None
        if canonical is not None:
            current = canonical
            sections.setdefault(current, [])
            continue
        if current is None:
            top.append(line)
        else:
            sections[current].append(line)
    return top, sections


# --- Contact ---------------------------------------------------------------
def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None


def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _parse_sex(text: str) -> str | None:
    match = _SEX.search(text)
    return match.group(1).capitalize() if match else None


def _parse_dob(text: str) -> str | None:
    raw = _first_group(_DOB, text)
    if not raw:
        return None
    # Normalize to ISO when parseable; otherwise keep the raw text for the officer.
    return _parse_date(raw, prefer_first=False) or raw


def _find_mobile(text: str) -> str | None:
    for candidate in _PHONE_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", candidate.group(0))
        if digits.startswith("63") and len(digits) == 12:
            digits = "0" + digits[2:]
        if re.fullmatch(r"09\d{9}", digits):
            return digits
    return None


# --- Name ------------------------------------------------------------------
def _looks_like_contact(line: str) -> bool:
    lowered = line.lower()
    if "@" in lowered or "http" in lowered:
        return True
    if any(keyword in lowered for keyword in _ADDRESS_KEYWORDS):
        return True
    return sum(character.isdigit() for character in line) >= 4


def _parse_name(top: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    for line in top:
        if _looks_like_contact(line):
            continue
        tokens = [token.strip(",") for token in line.split() if token.strip(",")]
        if not (1 <= len(tokens) <= 5) or not all(_is_name_token(token) for token in tokens):
            continue
        suffix: str | None = None
        if tokens and tokens[-1].lower() in _SUFFIXES:
            suffix = tokens.pop()
        if not tokens:
            continue
        if len(tokens) == 1:
            return tokens[0], None, None, suffix
        firstname, lastname = tokens[0], tokens[-1]
        middlename = " ".join(tokens[1:-1]) or None
        return firstname, middlename, lastname, suffix
    return None, None, None, None


def _is_name_token(token: str) -> bool:
    core = token.replace(".", "").replace("-", "")
    return bool(core) and core.isalpha()


# --- Skills ----------------------------------------------------------------
# A link / email / handle, or a "key: value" / "id=..." label — none of which are
# skills. Catches project links and prose that share the SKILLS block when a
# resume gives no clean section break. Kept structural (not a domain word list) so
# real skills like ".NET", "Socket.io" or "Node.js" are not falsely rejected.
_NON_SKILL = re.compile(r"https?://|www\.|[@=:]|\.[a-z]{2,}/", re.IGNORECASE)
# A sentence break (". A", "! The") marks bullet/description prose, not a skill.
_SKILL_SENTENCE = re.compile(r"[.!?]\s+\S")


def _looks_like_skill(token: str) -> bool:
    """Whether a token reads like a technical skill rather than prose / a link.

    Skills are short noun-phrases ("Material UI", "Unit Testing", "Hono-API"), so
    reject long or multi-clause text, links, and labels. Conservative on purpose —
    the officer can still add anything this drops (Human-in-the-Loop)."""
    if not token or len(token) > 60:
        return False
    if len(token.split()) > 4:  # skills are 1-4 words; longer is a phrase/sentence
        return False
    if token.endswith(".."):  # OCR bullet tails like "etc.."
        return False
    # Reject links / labels ("Tech Stack:", "id=...") and multi-clause prose.
    return not (_NON_SKILL.search(token) or _SKILL_SENTENCE.search(token))


def _parse_skills(section: list[str]) -> list[str]:
    skills: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,;\n•|]", "\n".join(section)):
        skill = raw.strip(" \t-•").strip()
        if not _looks_like_skill(skill):
            continue
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            skills.append(skill)
        if len(skills) >= _MAX_SKILLS:
            break
    return skills


# --- Education -------------------------------------------------------------
def _parse_education(section: list[str]) -> EducationalBackground | None:
    if not section:
        return None
    text = "\n".join(section)
    level = _line_containing(section, _EDUCATION_KEYWORDS)
    school = _line_containing(section, _SCHOOL_KEYWORDS)
    course = _line_containing(section, _COURSE_KEYWORDS)
    year_match = None
    for match in _YEAR.finditer(text):
        year_match = match.group(0)
    if not any((level, school, course, year_match)):
        return None
    return EducationalBackground(
        highest_education_level=_clip(level),
        school_university=_clip(school),
        course_program=_clip(course),
        year_graduated=year_match,
    )


def _line_containing(section: list[str], keywords: tuple[str, ...]) -> str | None:
    for line in section:
        lowered = line.lower()
        if any(keyword in lowered for keyword in keywords):
            return line
    return None


# --- Work experience -------------------------------------------------------
def _parse_work(section: list[str]) -> list[WorkExperience]:
    """Assemble work-experience entries from the WORK EXPERIENCE section.

    Each entry is anchored on a line that carries a **date range** — the reliable
    signal of a job header (e.g. ``"Software Engineer, Acme  Jan 2020 - Present"``).
    Lines without a date are wrapped description/bullet prose (or a continuation
    of the title) and must NOT each become their own entry: doing so exploded a
    multi-line job description into a dozen bogus "positions" (one per wrapped
    line). Such lines are skipped, except that a *date-only* header (its text
    empty once the dates are stripped) borrows the nearest preceding unused line
    as its title, covering resumes that place the role on the line above the dates.
    """
    entries: list[WorkExperience] = []
    # Most recent non-date line, kept only to title a following date-only header.
    prev_line: str | None = None
    for line in section:
        date_range = _DATE_RANGE.search(line)
        if date_range is None:
            # Description / continuation prose — never an entry on its own.
            stripped = line.strip()
            prev_line = stripped or prev_line
            continue
        start = _to_iso(date_range.group("start"))
        end = _to_iso(date_range.group("end"))
        position = _DATE_RANGE.sub("", line).strip(" \t-–—,")
        if not position and prev_line:
            position = prev_line  # role sat on the line above the dates
        prev_line = None
        entries.append(
            WorkExperience(
                position=_clip(position) or None,
                start_date=start,
                end_date=end,
            )
        )
        if len(entries) >= _MAX_WORK:
            break
    return entries


def _to_iso(token: str) -> str | None:
    """Parse a single date token to an ISO date, or None for ongoing/unparseable.

    Uses ``dateparser`` for real dates ("June 2018", "06/2018") and short-circuits
    bare years (which dateparser would fill with the current month) to Jan 1.
    """
    lowered = token.lower().strip()
    if not lowered or "present" in lowered or "current" in lowered:
        return None
    year = _YEAR.search(token)
    if year is not None and _HAS_MONTH.search(token) is None:
        return f"{year.group(0)}-01-01"
    return _parse_date(token, prefer_first=True)


def _parse_date(value: str, *, prefer_first: bool) -> str | None:
    settings: dict[str, object] = {"REQUIRE_PARTS": ["year"]}
    if prefer_first:
        settings["PREFER_DAY_OF_MONTH"] = "first"
    parsed = dateparser.parse(value, settings=settings)
    return parsed.date().isoformat() if parsed is not None else None


# --- Trainings / eligibility ----------------------------------------------
def _parse_trainings(section: list[str]) -> list[Training]:
    items: list[Training] = []
    for line in section[: _MAX_TRAININGS * 2]:
        title = _clip(line)
        if title:
            items.append(Training(training_title=title))
        if len(items) >= _MAX_TRAININGS:
            break
    return items


def _parse_eligibility(section: list[str]) -> list[Eligibility]:
    items: list[Eligibility] = []
    for line in section[: _MAX_ELIGIBILITY * 2]:
        title = _clip(line)
        if title:
            items.append(Eligibility(title=title))
        if len(items) >= _MAX_ELIGIBILITY:
            break
    return items


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip(" \t-–—•:")
    return trimmed[:_MAX_ITEM_LEN] if trimmed else None
