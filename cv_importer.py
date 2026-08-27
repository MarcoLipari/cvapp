"""Import an existing PDF, Markdown, or text CV into reusable CV Manager sections."""
from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


SECTION_CATEGORIES = {
    "EDUCATION": ("Education", "Education"),
    "EXPERIENCE": ("Experience", "Experience"),
    "WORK EXPERIENCE": ("Experience", "Experience"),
    "PROFESSIONAL EXPERIENCE": ("Experience", "Experience"),
    "RELEVANT PROJECTS": ("Relevant Projects", "Projects"),
    "RELEVANT PROJECTS & ACTIVITIES": ("Relevant Projects & Activities", "Projects"),
    "PROJECTS": ("Projects", "Projects"),
    "SKILLS": ("Skills", "Skills"),
    "TECHNICAL SKILLS": ("Technical Skills", "Skills"),
    "ACTIVITIES": ("Activities", "Other"),
    "CERTIFICATIONS": ("Certifications", "Other"),
}


@dataclass(frozen=True)
class ImportedSection:
    title: str
    category: str
    content: str


@dataclass(frozen=True)
class ImportResult:
    sections: list[ImportedSection]
    profile: dict[str, str]


def extract_cv_text(path: str | Path) -> str:
    """Return layout-preserving text from a supported CV file."""
    source = Path(path)
    if source.suffix.lower() in {".md", ".txt"}:
        return source.read_text(encoding="utf-8")
    if source.suffix.lower() != ".pdf":
        raise ValueError("Choose a PDF, Markdown, or plain-text CV.")
    if not shutil.which("pdftotext"):
        raise RuntimeError("PDF import needs the pdftotext command. Install Poppler, then try again.")
    result = subprocess.run(["pdftotext", "-layout", str(source), "-"], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "This PDF could not be read.")
    if not result.stdout.strip():
        raise RuntimeError("No selectable text was found in this PDF. Scan/OCR it first, then import the text version.")
    return result.stdout


def import_cv(path: str | Path) -> ImportResult:
    source = Path(path)
    text = extract_cv_text(source)
    if source.suffix.lower() == ".pdf" and shutil.which("pdftohtml"):
        text = _restore_pdf_links(text, _extract_pdf_links(source))
    return parse_cv_text(text)


def _extract_pdf_links(path: Path) -> list[tuple[str, str]]:
    """Return visible labels and destinations from PDF link annotations."""
    result = subprocess.run(
        ["pdftohtml", "-xml", "-stdout", "-i", "-hidden", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        return []
    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError:
        return []

    links: list[tuple[str, str]] = []
    for page in root.findall("page"):
        fragments: dict[tuple[str, str], list[tuple[int, str]]] = {}
        for text_node in page.findall("text"):
            top = text_node.get("top", "")
            left = int(text_node.get("left", "0"))
            for anchor in text_node.iter("a"):
                href = anchor.get("href", "")
                if href.startswith(("http://", "https://")):
                    fragments.setdefault((top, href), []).append(
                        (left, "".join(anchor.itertext()))
                    )
        for (_, href), pieces in fragments.items():
            label = re.sub(r"\s+", " ", "".join(
                text for _, text in sorted(pieces)
            )).strip()
            if label:
                links.append((label, href))
    return links


def _restore_pdf_links(text: str, links: list[tuple[str, str]]) -> str:
    """Restore PDF annotations as Markdown links in imported section content."""
    lines = text.splitlines(keepends=True)
    first_heading = next(
        (index for index, line in enumerate(lines) if _heading(line)),
        len(lines),
    )
    header = "".join(lines[:first_heading])
    content = "".join(lines[first_heading:])
    for label, url in links:
        content = re.sub(
            rf"(?<!\w){re.escape(label)}(?!\w)",
            lambda _: f"[{label}]({url})",
            content,
            count=1,
        )
    return header + content


def parse_cv_text(text: str) -> ImportResult:
    """Turn conventional CV text into app sections and optional personal details.

    Layout-preserving PDF text commonly separates dates/locations using several
    spaces. Those columns become ``left :: right`` so the app's PDF template
    restores the right-aligned layout.
    """
    raw_lines = [line.rstrip().replace("\u2022", "•") for line in text.replace("\f", "").splitlines()]
    first_heading = next((index for index, line in enumerate(raw_lines) if _heading(line)), len(raw_lines))
    profile = _profile_from_header(raw_lines[:first_heading])
    sections: list[ImportedSection] = []
    current_title: str | None = None
    current_category: str | None = None
    content: list[str] = []

    def finish() -> None:
        nonlocal content
        if current_title and content:
            cleaned = "\n".join(content).strip()
            if cleaned:
                sections.append(ImportedSection(current_title, current_category or "Other", cleaned))
        content = []

    for raw in raw_lines[first_heading:]:
        match = _heading(raw)
        if match:
            finish()
            current_title, current_category = match
            continue
        if not current_title:
            continue
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^[•*-]\s+", line):
            content.append("- " + re.sub(r"^[•*-]\s+", "", line))
        else:
            formatted = _format_layout_line(raw)
            # A layout row with a date/location column begins a new entry even
            # when it follows a wrapped bullet from the previous entry.
            if content and content[-1].startswith("- ") and " :: " not in formatted:
                content[-1] += " " + line
            else:
                content.append(formatted)
    finish()
    return ImportResult(sections, profile)


def _heading(line: str) -> tuple[str, str] | None:
    candidate = re.sub(r"\s+", " ", line.strip()).upper()
    return SECTION_CATEGORIES.get(candidate)


def _format_layout_line(raw: str) -> str:
    line = raw.strip()
    columns = re.split(r"\s{3,}", line, maxsplit=1)
    if len(columns) != 2:
        return line
    left, right = (part.strip() for part in columns)
    if not right or len(right) > 60 or not _metadata_column(right):
        return line
    if re.search(r"\d{4}|present|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", right, re.I):
        return f"**{left}** :: *{right}*"
    return f"*{left}* :: *{right}*"


def _metadata_column(value: str) -> bool:
    return bool(re.search(r"\d{4}|present|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|,", value, re.I))


def _profile_from_header(lines: list[str]) -> dict[str, str]:
    header = " ".join(line.strip() for line in lines if line.strip())
    profile: dict[str, str] = {}
    name = next((line.strip().title() for line in lines if re.fullmatch(r"[A-Z][A-Z .'-]{3,}", line.strip())), "")
    if name:
        profile["name"] = name
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", header)
    if email:
        profile["email"] = email.group(0)
    phone = re.search(r"(?:\+?\d|\(\d)[\d .()\-]{7,}\d", header)
    if phone:
        profile["phone"] = phone.group(0).strip()
    header_without_email = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "", header)
    domains = re.findall(r"(?:https?://)?(?:www\.)?[\w.-]+\.[A-Za-z]{2,}(?:/[\w./-]*)?", header_without_email)
    for domain in domains:
        clean = domain.removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip(".,")
        if "github.com" in clean.lower():
            profile["github"] = clean
        elif "." in clean and "email" not in clean.lower() and "@" not in clean:
            profile.setdefault("website", clean)
    return profile
