"""
Resume extractor — parses PDF and DOCX resumes.

Uses pdfplumber for PDF, python-docx for DOCX.
Extracts raw text then applies regex/heuristic patterns to identify
fields: email, phone, skills, education, name, etc.

Extraction method is tagged as REGEX or HEURISTIC because we're
inferring structure from unstructured text.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List

from src.schema import RawField, ExtractionMethod
from src.extract.base import BaseExtractor

logger = logging.getLogger(__name__)

# Common skill keywords to look for in resumes
SKILL_KEYWORDS = {
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang",
    "rust", "ruby", "php", "swift", "kotlin", "scala", "r", "matlab",
    "sql", "nosql", "mongodb", "postgresql", "mysql", "redis", "elasticsearch",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "jenkins",
    "react", "angular", "vue", "node.js", "nodejs", "django", "flask",
    "spring", "fastapi", "express", "next.js", "nextjs",
    "machine learning", "deep learning", "nlp", "computer vision",
    "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy",
    "html", "css", "sass", "graphql", "rest", "restful",
    "git", "linux", "agile", "scrum", "ci/cd", "devops",
    "figma", "photoshop", "illustrator",
    "tableau", "power bi", "excel", "spark", "hadoop", "kafka",
    "microservices", "api design", "system design", "data structures",
}

# Regex patterns for field extraction
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
PHONE_PATTERN = re.compile(
    r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}'
)
LINKEDIN_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?|LinkedIn/[A-Za-z0-9_-]+/?',
    re.IGNORECASE
)
GITHUB_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+/?'
)
EDUCATION_KEYWORDS = [
    "bachelor", "master", "phd", "ph.d", "mba", "b.s.", "m.s.",
    "b.tech", "m.tech", "b.e.", "m.e.", "bsc", "msc",
    "university", "college", "institute", "school of",
]
CERT_KEYWORDS = [
    "certified", "certification", "certificate", "aws certified",
    "google certified", "pmp", "scrum master", "cissp", "cka",
    "ckad", "azure certified", "comptia",
]
EXPERIENCE_PATTERN = re.compile(
    r'(\d{1,2})\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)?',
    re.IGNORECASE,
)


def _extract_text_from_pdf(path: Path) -> str:
    """
    Extract all text from a PDF file.

    Strategy:
      1. Try pdfplumber (works for text-based PDFs)
      2. If no text extracted (scanned/image PDF), fall back to OCR
         via pytesseract + pdf2image or Pillow (if installed)
      3. Gracefully degrade if OCR libraries aren't available
    """
    text = ""

    # --- Phase 1: pdfplumber text extraction ---
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        text = "\n".join(text_parts)
    except Exception as e:
        logger.error("Error extracting PDF text from %s: %s", path, e)

    # --- Phase 2: OCR fallback for scanned PDFs ---
    if not text.strip():
        logger.info("No text from pdfplumber for %s, attempting OCR fallback...", path.name)
        text = _ocr_pdf_fallback(path)

    return text


def _ocr_pdf_fallback(path: Path) -> str:
    """
    OCR fallback for scanned/image PDFs.

    Uses pytesseract + Pillow to extract text from PDF page images.
    Returns empty string if OCR libraries are not installed (graceful degradation).
    """
    # Try pytesseract + pdfplumber's page images
    try:
        import pytesseract
        import pdfplumber

        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                # Convert page to image and OCR it
                img = page.to_image(resolution=300)
                pil_image = img.original
                ocr_text = pytesseract.image_to_string(pil_image)
                if ocr_text.strip():
                    text_parts.append(ocr_text)

        if text_parts:
            logger.info("OCR extracted text from %d pages of %s", len(text_parts), path.name)
            return "\n".join(text_parts)

    except ImportError:
        logger.debug(
            "OCR libraries not installed (pytesseract). "
            "Install with: pip install pytesseract. "
            "Also requires Tesseract OCR engine on system PATH."
        )
    except Exception as e:
        logger.warning("OCR fallback failed for %s: %s", path.name, e)

    return ""


def _extract_text_from_docx(path: Path) -> str:
    """Extract all text from a DOCX file using python-docx."""
    try:
        from docx import Document
        doc = Document(path)
        return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except Exception as e:
        logger.error("Error extracting DOCX text from %s: %s", path, e)
        return ""


class ResumeExtractor(BaseExtractor):
    """Extract candidate data from PDF or DOCX resumes."""

    source_name = "resume"

    def extract(self, source_path: str | Path) -> Dict[str, List[RawField]]:
        source_path = Path(source_path)
        results: Dict[str, List[RawField]] = {}

        if not source_path.exists():
            logger.error("Resume file not found: %s", source_path)
            return results

        # Extract raw text based on file type
        suffix = source_path.suffix.lower()
        if suffix == ".pdf":
            text = _extract_text_from_pdf(source_path)
        elif suffix in (".docx", ".doc"):
            text = _extract_text_from_docx(source_path)
        else:
            logger.warning("Unsupported resume format: %s", suffix)
            return results

        if not text.strip():
            logger.warning("No text extracted from resume: %s", source_path)
            return results

        fields = self._extract_fields_from_text(text, source_path.name)

        if fields:
            # Use email as candidate key if found
            candidate_key = None
            for f in fields:
                if f.field == "email" and f.value:
                    candidate_key = str(f.value).lower().strip()
                    break
            if candidate_key is None:
                candidate_key = f"resume_{source_path.stem}"

            results[candidate_key] = fields

        logger.info("Resume extractor: extracted %d candidates from %s", len(results), source_path)
        return results

    def _extract_fields_from_text(self, text: str, filename: str) -> List[RawField]:
        """Apply regex and heuristic patterns to extract fields from text."""
        text = text.replace('\ufffd', '-')
        
        fields: List[RawField] = []
        source_id = f"file_{filename}"
        text_lower = text.lower()

        # --- Name and Location (heuristic from first few lines) ---
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            first_line = lines[0]
            # Heuristic: first non-empty line is often the candidate name
            words = first_line.split()
            if 1 <= len(words) <= 5 and not any(c in first_line for c in "@.:/"):
                fields.append(RawField(
                    field="full_name",
                    value=first_line,
                    source=self.source_name,
                    source_id=source_id,
                    extraction_method=ExtractionMethod.HEURISTIC,
                ))

            # Look for location in the next 2 lines (e.g., "West Bengal, Kolkata 721401" or "San Francisco, CA")
            for line in lines[1:4]:
                if ("," in line or re.search(r'\b\d{5,6}\b', line)) and not any(c in line for c in "@:/+"):
                    if len(line) < 50:
                        fields.append(RawField(
                            field="location",
                            value=line,
                            source=self.source_name,
                            source_id=source_id,
                            extraction_method=ExtractionMethod.HEURISTIC,
                        ))
                        break

        # --- Email ---
        emails = EMAIL_PATTERN.findall(text)
        if emails:
            fields.append(RawField(
                field="email",
                value=emails[0],
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.REGEX,
            ))

        # --- Phone ---
        phones = PHONE_PATTERN.findall(text)
        if phones:
            valid_phones = [p for p in phones if len(re.sub(r'\D', '', p)) >= 7]
            if valid_phones:
                fields.append(RawField(
                    field="phone",
                    value=valid_phones[0],
                    source=self.source_name,
                    source_id=source_id,
                    extraction_method=ExtractionMethod.REGEX,
                ))

        # --- LinkedIn URL ---
        linkedin_matches = LINKEDIN_PATTERN.findall(text)
        if linkedin_matches:
            url = linkedin_matches[0]
            if url.lower().startswith("linkedin/"):
                username = url.split("/")[1]
                url = f"https://linkedin.com/in/{username}"
            elif not url.startswith("http"):
                url = "https://" + url
            fields.append(RawField(
                field="linkedin_url",
                value=url,
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.REGEX,
            ))

        # --- GitHub URL ---
        github_matches = GITHUB_PATTERN.findall(text)
        if github_matches:
            url = github_matches[0]
            if not url.startswith("http"):
                url = "https://" + url
            fields.append(RawField(
                field="github_url",
                value=url,
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.REGEX,
            ))

        # --- Skills ---
        found_skills = []
        for skill in SKILL_KEYWORDS:
            # Word boundary match (case insensitive)
            pattern = re.compile(r'\b' + re.escape(skill) + r'\b', re.IGNORECASE)
            if pattern.search(text):
                found_skills.append(skill.title() if len(skill) > 3 else skill.upper())
        if found_skills:
            fields.append(RawField(
                field="skills",
                value=found_skills,
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.HEURISTIC,
            ))

        # --- Years of Experience ---
        exp_match = EXPERIENCE_PATTERN.search(text)
        if exp_match:
            fields.append(RawField(
                field="years_of_experience",
                value=float(exp_match.group(1)),
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.REGEX,
            ))

        # --- Section-based parsing (Education, Certifications) ---
        sections = {"education": [], "certifications": [], "experience": []}
        current_section = None

        for line in lines:
            line_clean = line.strip().lower()
            # Detect section headers (short lines with specific keywords)
            if len(line_clean) < 30:
                if line_clean in ["education", "academic background", "academics"]:
                    current_section = "education"
                    continue
                elif line_clean in ["certificates", "certifications", "licenses & certifications"]:
                    current_section = "certifications"
                    continue
                elif line_clean in ["experience", "work experience", "employment history", "professional experience"]:
                    current_section = "experience"
                    continue
                # If it's a short line that might be a new section like "Projects", "Skills", etc.
                elif line_clean in ["projects", "skills", "technical skills", "achievements", "summary", "languages"]:
                    current_section = None
                    continue
            
            if current_section and line.strip():
                sections[current_section].append(line.strip())

        if sections["education"]:
            fields.append(RawField(
                field="education",
                value="; ".join(sections["education"][:4]), # Grab first few lines of education
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.HEURISTIC,
            ))

        if sections["certifications"]:
            # Filter out lines that are literally just "Certificate" or dates
            valid_certs = [c for c in sections["certifications"] if len(c) > 15 and not c.lower() == "certificate"]
            if valid_certs:
                fields.append(RawField(
                    field="certifications",
                    value=valid_certs[:5],
                    source=self.source_name,
                    source_id=source_id,
                    extraction_method=ExtractionMethod.HEURISTIC,
                ))

        return fields
