import pytest

from personal_rag.pdf import PdfError, extract_pdf_text


def make_pdf(text: str) -> bytes:
    """Minimal one-page PDF with a text string."""
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET"
    objs[3] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_extracts_text() -> None:
    assert "hello pdf" in extract_pdf_text(make_pdf("hello pdf"), 5, 10)


def test_rejects_non_pdf() -> None:
    with pytest.raises(PdfError):
        extract_pdf_text(b"MZ not a pdf", 5, 10)


def test_garbage_pdf_fails_safely() -> None:
    with pytest.raises(PdfError):
        extract_pdf_text(b"%PDF-1.4\n\x00\xff garbage", 5, 10)
