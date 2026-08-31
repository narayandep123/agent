import io

from PIL import Image

from app.services import document_verification_service as verifier


def _scan() -> bytes:
    image = Image.new("RGB", (900, 600), "white")
    # Add strong contrast so the local quality gate passes.
    for x in range(100, 800):
        for y in range(100, 500):
            if (x // 20 + y // 20) % 2:
                image.putpixel((x, y), (20, 20, 20))
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_matching_document_is_verified(monkeypatch):
    monkeypatch.setattr(verifier, "_vision_extract", lambda *_: {
        "document_type": "ID", "legible": True, "full_name": "Test Student",
        "soa_id": "CS-1", "confidence": .96, "findings": [],
    })
    result = verifier.verify(_scan(), "image/png", "id.png", "ID", "Test Student", "CS-1")
    assert result.status == "VERIFIED"
    assert result.name_match is True
    assert result.soa_id_match is True


def test_name_mismatch_is_flagged_and_not_verified(monkeypatch):
    monkeypatch.setattr(verifier, "_vision_extract", lambda *_: {
        "document_type": "ID", "legible": True, "full_name": "Someone Else",
        "soa_id": "CS-1", "confidence": .93, "findings": [],
    })
    result = verifier.verify(_scan(), "image/png", "id.png", "ID", "Test Student", "CS-1")
    assert result.status == "NEEDS_CORRECTION"
    assert any("Name mismatch" in finding for finding in result.findings)


def test_unavailable_vision_fails_safe_to_manual_review(monkeypatch):
    monkeypatch.setattr(verifier, "_vision_extract", lambda *_: None)
    result = verifier.verify(_scan(), "image/png", "id.png", "ID", "Test Student", "CS-1")
    assert result.status == "MANUAL_REVIEW"
    assert result.name_match is None


def test_embedded_image_override_is_ignored_and_routed_to_manual_review(monkeypatch):
    monkeypatch.setattr(verifier, "_vision_extract", lambda *_: {
        "document_type": "ID", "legible": True, "full_name": "Test Student",
        "soa_id": "CS-1", "confidence": .99, "findings": [],
        "instruction_override_detected": True,
    })
    result = verifier.verify(_scan(), "image/png", "id.png", "ID", "Test Student", "CS-1")
    assert result.status == "MANUAL_REVIEW"
    assert result.analyzer == "gemini-vision-security-guard"
    assert any("instructions" in finding for finding in result.findings)


def test_pdf_document_is_accepted_and_verified(monkeypatch):
    monkeypatch.setattr(verifier, "_vision_extract", lambda *_: {
        "document_type": "ID", "legible": True, "full_name": "Test Student",
        "soa_id": "CS-1", "confidence": .97, "findings": [],
    })
    pdf = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\nstartxref\n0\n%%EOF"
    result = verifier.verify(pdf, "application/pdf", "student-id.pdf", "ID", "Test Student", "CS-1")
    assert result.status == "VERIFIED"
    assert result.soa_id_match is True


def test_invalid_or_encrypted_pdf_is_rejected_before_model(monkeypatch):
    called = False
    def fake_extract(*_args):
        nonlocal called
        called = True
    monkeypatch.setattr(verifier, "_vision_extract", fake_extract)
    invalid = verifier.verify(b"not a pdf", "application/pdf", "bad.pdf", "ID", "Test Student", "CS-1")
    encrypted = verifier.verify(b"%PDF-1.7 /Encrypt\n%%EOF", "application/pdf", "locked.pdf", "ID", "Test Student", "CS-1")
    assert invalid.status == encrypted.status == "NEEDS_CORRECTION"
    assert called is False
