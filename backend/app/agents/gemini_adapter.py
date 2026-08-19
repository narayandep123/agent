"""Gemini-backed proposal planner with a fail-closed deterministic fallback.

Gemini may identify and structure work, but it is never given executable tools.
Every proposed task is validated here and then evaluated by deterministic code.
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

SUPPORTED_INTENTS = {"MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "GRIEVANCE", "POLICY_QUESTION", "UNSUPPORTED"}


class TaskEntities(BaseModel):
    location: str = "Not specified"
    floor: str = "Not specified"
    issue: str = "Not specified"
    space: str = "Not specified"
    date: str = "Not specified"
    time: str = "Not specified"
    seat: str = "Not specified"
    certificate_type: str = "Not specified"
    policy_topic: str = "Not specified"
    grievance_summary: str = "Not specified"


class ProposedTask(BaseModel):
    intent: Literal["MAINTENANCE", "LAB_BOOKING", "CERTIFICATE", "GRIEVANCE", "POLICY_QUESTION", "UNSUPPORTED"]
    summary: str = Field(max_length=240)
    entities: TaskEntities = Field(default_factory=TaskEntities)


class AgentPlan(BaseModel):
    tasks: list[ProposedTask] = Field(min_length=1, max_length=6)
    language: Literal["en", "hi", "hinglish"] = "en"
    urgency: Literal["NORMAL", "HIGH", "EMERGENCY"] = "NORMAL"


_SYSTEM_PROMPT = """You are Campus Copilot's planning node. Analyse only the latest user message.
Do not execute actions, approve requests, invent policy, or carry an earlier topic into an unrelated new request.
Return each independently requested task in order.

MAINTENANCE means broken campus facilities. LAB_BOOKING means reserving a campus lab, library seat,
classroom, or study room. CERTIFICATE means requesting a bonafide, enrolment, transcript, marksheet,
or character certificate. GRIEVANCE means a campus complaint, harassment, teasing/eve-teasing,
bullying, stalking, ragging, discrimination, safety concern, or unfair treatment. POLICY_QUESTION asks what an official campus rule says.
Everything outside institutional campus services is UNSUPPORTED.

Use "Not specified" for missing values. Never infer names, dates, locations, policy facts, or approval.
Set HIGH for serious distress, threats, harassment, teasing, bullying, ragging, discrimination, or safety concerns;
EMERGENCY only for immediate danger. Video creation, flights, and cabs are UNSUPPORTED, and their
summary must describe the actual current request, never an earlier example."""


def is_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _coerce(plan: AgentPlan) -> dict | None:
    tasks = []
    for task in plan.tasks:
        intent = task.intent.upper().strip()
        if intent not in SUPPORTED_INTENTS:
            continue
        entities = {
            str(key)[:60]: str(value)[:300]
            for key, value in task.entities.model_dump().items()
            if value != "Not specified"
        }
        tasks.append({"intent": intent, "summary": task.summary.strip()[:240], "entities": entities})
    if not tasks:
        return None
    return {"tasks": tasks, "language": plan.language, "urgency": plan.urgency}


def plan(text: str) -> dict | None:
    """Return a schema-validated plan or ``None`` so callers can fall back safely."""
    if not is_enabled():
        return None
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=12_000),
        )
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=AgentPlan,
                temperature=0,
            ),
        )
        parsed = response.parsed
        if isinstance(parsed, AgentPlan):
            return _coerce(parsed)
        return _coerce(AgentPlan.model_validate_json(response.text or ""))
    except Exception:
        return None


def propose(text: str) -> dict | None:
    """Backward-compatible single-task proposal used by the current dispatcher."""
    result = plan(text)
    if not result or not result["tasks"]:
        return None
    first = result["tasks"][0]
    return {**first, "language": result["language"], "urgency": result["urgency"]}
