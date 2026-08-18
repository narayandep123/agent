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
        "roll_no": "CS-1", "confidence": .96, "findings": [],
    })
    result = verifier.verify(_scan(), "image/png", "id.png", "ID", "Test Student", "CS-1")
    assert result.status == "VERIFIED"
    assert result.name_match is True
    assert result.roll_no_match is True


def test_name_mismatch_is_flagged_and_not_verified(monkeypatch):
    monkeypatch.setattr(verifier, "_vision_extract", lambda *_: {
        "document_type": "ID", "legible": True, "full_name": "Someone Else",
        "roll_no": "CS-1", "confidence": .93, "findings": [],
    })
    result = verifier.verify(_scan(), "image/png", "id.png", "ID", "Test Student", "CS-1")
    assert result.status == "NEEDS_CORRECTION"
    assert any("Name mismatch" in finding for finding in result.findings)


def test_unavailable_vision_fails_safe_to_manual_review(monkeypatch):
    monkeypatch.setattr(verifier, "_vision_extract", lambda *_: None)
    result = verifier.verify(_scan(), "image/png", "id.png", "ID", "Test Student", "CS-1")
    assert result.status == "MANUAL_REVIEW"
    assert result.name_match is None
