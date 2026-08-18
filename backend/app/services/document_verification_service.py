"""Multimodal document checks used before certificate approval.

The vision model extracts evidence only. Identity matching and the routing verdict
remain deterministic so a model can never approve or issue a certificate.
"""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, asdict

from PIL import Image, ImageStat, UnidentifiedImageError


ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 8 * 1024 * 1024


@dataclass
class Verification:
    document_type: str
    filename: str
    legible: bool
    expected_format: bool
    extracted_name: str
    extracted_roll_no: str
    name_match: bool | None
    roll_no_match: bool | None
    confidence: float
    status: str
    findings: list[str]
    analyzer: str

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _local_quality(content: bytes) -> tuple[bool, list[str]]:
    """Reject obviously unusable scans before sending any data to a model."""
    try:
        image = Image.open(io.BytesIO(content))
        image.verify()
        image = Image.open(io.BytesIO(content)).convert("L")
    except (UnidentifiedImageError, OSError):
        return False, ["The upload is not a readable JPEG, PNG, or WebP image."]
    findings = []
    if image.width < 600 or image.height < 400:
        findings.append(f"Resolution is too low ({image.width}×{image.height}); upload at least 600×400.")
    if ImageStat.Stat(image.resize((128, 128))).stddev[0] < 12:
        findings.append("The scan has too little contrast to read reliably.")
    return not findings, findings


def _vision_extract(content: bytes, mime_type: str, document_type: str) -> dict | None:
    """Return bounded evidence from Gemini, or None when vision is unavailable."""
    if not os.getenv("GEMINI_API_KEY"):
        return None
    try:
        import google.generativeai as genai  # type: ignore

        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel(os.getenv("GEMINI_VISION_MODEL", os.getenv("GEMINI_MODEL", "gemini-1.5-flash")))
        prompt = f"""Inspect this campus {document_type} scan. Extract evidence only; do not approve anything.
Return one JSON object with: document_type (ID or MARKSHEET or OTHER), legible (boolean),
full_name (string), roll_no (string), confidence (0 to 1), findings (array of short strings).
Mark legible false when important identity text is cropped, blurred, obscured, or unreadable.
Never infer missing text. JSON only."""
        response = model.generate_content(
            [prompt, {"mime_type": mime_type, "data": content}],
            generation_config={"response_mime_type": "application/json", "temperature": 0.0},
        )
        raw = json.loads((response.text or "").strip())
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def verify(content: bytes, mime_type: str, filename: str, document_type: str,
           expected_name: str, expected_roll_no: str) -> Verification:
    document_type = document_type.upper()
    if mime_type not in ALLOWED_TYPES:
        raise ValueError("Upload a JPEG, PNG, or WebP scan.")
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_BYTES:
        raise ValueError("The document must be 8 MB or smaller.")
    if document_type not in {"ID", "MARKSHEET"}:
        raise ValueError("Document type must be ID or MARKSHEET.")

    quality_ok, findings = _local_quality(content)
    if not quality_ok:
        return Verification(document_type, filename, False, False, "", "", None, None,
                            0.0, "NEEDS_CORRECTION", findings, "local-quality-check")

    evidence = _vision_extract(content, mime_type, document_type)
    if evidence is None:
        return Verification(document_type, filename, True, False, "", "", None, None,
                            0.0, "MANUAL_REVIEW", ["Automated visual extraction is unavailable; an administrator must verify the scan."],
                            "manual-fallback")

    extracted_name = str(evidence.get("full_name", "")).strip()
    extracted_roll = str(evidence.get("roll_no", "")).strip()
    legible = bool(evidence.get("legible", False))
    detected_type = str(evidence.get("document_type", "OTHER")).upper()
    expected_format = detected_type == document_type
    name_match = bool(extracted_name) and _norm(extracted_name) == _norm(expected_name)
    roll_match = None if not expected_roll_no else bool(extracted_roll) and _norm(extracted_roll) == _norm(expected_roll_no)
    model_findings = evidence.get("findings", [])
    if isinstance(model_findings, list):
        findings.extend(str(item)[:180] for item in model_findings[:5])
    if not legible:
        findings.append("Important identity text is not legible enough to verify.")
    if not expected_format:
        findings.append(f"Expected {document_type}, but the scan appears to be {detected_type}.")
    if not name_match:
        findings.append(f"Name mismatch: document says '{extracted_name or 'unreadable'}'; enrollment record says '{expected_name}'.")
    if roll_match is False:
        findings.append(f"Roll number mismatch: document says '{extracted_roll or 'unreadable'}'; enrollment record says '{expected_roll_no}'.")

    passed = legible and expected_format and name_match and roll_match is not False
    return Verification(document_type, filename, legible, expected_format, extracted_name, extracted_roll,
                        name_match, roll_match, max(0.0, min(float(evidence.get("confidence", 0)), 1.0)),
                        "VERIFIED" if passed else "NEEDS_CORRECTION", findings or ["Document identity fields match the enrollment record."],
                        "gemini-vision")
