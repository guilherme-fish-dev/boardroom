import io

from pypdf import PdfReader, PdfWriter

from app.pdf_extract import extract_text


def _minimal_pdf_bytes(lines: list[str]) -> bytes:
    """Build a minimal single-page PDF containing the given lines of text, using the
    standard Helvetica font, entirely by hand (no PDF-generation dependency needed just
    for tests) — good enough for pypdf to read back with extract_text()."""
    content_lines = "\n".join(
        f"BT /F1 24 Tf 72 {700 - i * 30} Td ({line}) Tj ET" for i, line in enumerate(lines)
    )
    content = content_lines.encode("latin-1")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    stream = b"stream\n" + content + b"\nendstream\n"
    objects.append(("5 0 obj\n<< /Length %d >>\n" % len(content)).encode("ascii") + stream + b"endobj\n")

    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_offset = len(pdf)
    pdf += ("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += ("%010d 00000 n \n" % off).encode("ascii")
    pdf += (
        "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objects) + 1, xref_offset)
    ).encode("ascii")
    return pdf


def _minimal_blank_pdf_bytes() -> bytes:
    """A minimal single-page PDF with no content stream at all — no extractable text."""
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << >> /MediaBox [0 0 612 792] >>\nendobj\n",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_offset = len(pdf)
    pdf += ("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += ("%010d 00000 n \n" % off).encode("ascii")
    pdf += (
        "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objects) + 1, xref_offset)
    ).encode("ascii")
    return pdf


def test_extract_text_returns_single_page_text(tmp_path):
    pdf_path = tmp_path / "single.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes(["Hello World", "Second line"]))

    result = extract_text(str(pdf_path))

    assert result == "Hello World\nSecond line"


def test_extract_text_concatenates_multiple_pages(tmp_path):
    writer = PdfWriter()
    for text in ["Page one text", "Page two text"]:
        reader = PdfReader(io.BytesIO(_minimal_pdf_bytes([text])))
        writer.add_page(reader.pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "multi.pdf"
    pdf_path.write_bytes(buf.getvalue())

    result = extract_text(str(pdf_path))

    assert result == "Page one text\n\nPage two text"


def test_extract_text_returns_empty_string_for_blank_page(tmp_path):
    pdf_path = tmp_path / "blank.pdf"
    pdf_path.write_bytes(_minimal_blank_pdf_bytes())

    result = extract_text(str(pdf_path))

    assert result == ""


def test_extract_text_truncates_long_text(tmp_path):
    pdf_path = tmp_path / "long.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes(["x" * 50]))

    result = extract_text(str(pdf_path), max_chars=10)

    assert result == "x" * 10 + "\n\n[texto truncado — o PDF tem mais conteúdo do que o mostrado aqui]"


def test_extract_text_raises_for_corrupt_file(tmp_path):
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"not a real pdf file, just random bytes 12345")

    try:
        extract_text(str(pdf_path))
        assert False, "expected an exception for a corrupt PDF"
    except Exception:
        pass
