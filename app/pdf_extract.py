from __future__ import annotations

from pypdf import PdfReader

MAX_PDF_TEXT_CHARS = 20_000
TRUNCATION_NOTICE = "\n\n[texto truncado — o PDF tem mais conteúdo do que o mostrado aqui]"


def extract_text(pdf_path: str, *, max_chars: int = MAX_PDF_TEXT_CHARS) -> str:
    """Extract and concatenate the text of every page of a PDF, truncating if it exceeds
    max_chars. Returns an empty string (after stripping whitespace) if no page has
    extractable text — e.g. a scanned document with no text layer. Callers decide what to
    do with that case (this module only extracts, it doesn't interpret the result).

    Raises whatever pypdf raises for a corrupt, invalid, or password-protected file — not
    caught here, propagates to the caller."""
    reader = PdfReader(pdf_path)
    pages_text = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n\n".join(pages_text).strip()
    if len(full_text) > max_chars:
        return full_text[:max_chars] + TRUNCATION_NOTICE
    return full_text
