import asyncio
import time

import pytest
from pydantic import ValidationError

from app.agents.gemini_adapter import AgentPlan
from app.agents.proposal import propose_plan
from app.api.routes import _normalize_parallel_safety
from app.api.routes import _execute_task_plan_async, _run_task_once
from app.db import SessionLocal
from app.services import task_plan_service


def test_deterministic_plan_does_not_treat_connector_order_as_dependency(monkeypatch):
    monkeypatch.setattr("app.agents.gemini_adapter.plan", lambda _text, _plan=None: None)
    tasks, urgency, source = propose_plan(
        "Tell me the hostel policy and also report the broken AC in Building LH room 123 ground floor "
        "then request a bonafide certificate"
    )
    assert source == "deterministic"
    assert urgency == "NORMAL"
    assert [task["task_id"] for task in tasks] == ["t1", "t2", "t3"]
    assert tasks[1]["depends_on"] == []
    assert tasks[2]["depends_on"] == []
    assert all("missing_params" in task and "parallel_safe" in task for task in tasks)


def test_plan_schema_rejects_unknown_and_cyclic_dependencies():
    base = {
        "task_id": "t1", "type": "POLICY_QUESTION", "summary": "Read policy",
        "known_params": {}, "missing_params": [], "parallel_safe": True,
    }
    with pytest.raises(ValidationError):
        AgentPlan.model_validate({"tasks": [{**base, "depends_on": ["missing"]}]})
    with pytest.raises(ValidationError):
        AgentPlan.model_validate({"tasks": [
            {**base, "depends_on": ["t2"]},
            {**base, "task_id": "t2", "depends_on": ["t1"]},
        ]})


def test_independent_task_runs_when_another_task_needs_information(client, student_headers, monkeypatch):
    monkeypatch.setattr("app.api.routes.propose_plan", lambda *_args, **_kwargs: ([
        {
            "task_id": "t1", "intent": "MAINTENANCE", "summary": "The AC is broken",
            "known_params": {"issue": "Air conditioner"}, "entities": {"issue": "Air conditioner"},
            "missing_params": ["location", "floor"], "depends_on": [], "parallel_safe": False,
        },
        {
            "task_id": "t2", "intent": "POLICY_QUESTION", "summary": "Tell me the hostel policy",
            "known_params": {"policy_topic": "hostel"}, "entities": {"policy_topic": "hostel"},
            "missing_params": [], "depends_on": [], "parallel_safe": True,
        },
    ], "NORMAL", "test"))
    response = client.post(
        "/api/v1/assistant", json={"text": "Handle both campus tasks"}, headers=student_headers,
    ).json()
    by_id = {item["task_id"]: item for item in response["outputs"]}
    assert response["type"] == "compound"
    assert by_id["t1"]["task_status"] == "PENDING"
    assert by_id["t2"]["task_status"] == "COMPLETED"
    assert by_id["t2"]["sources"]


def test_failed_or_waiting_prerequisite_blocks_dependent_task(client, student_headers, monkeypatch):
    monkeypatch.setattr("app.api.routes.propose_plan", lambda *_args, **_kwargs: ([
        {
            "task_id": "t1", "intent": "MAINTENANCE", "summary": "The projector is broken",
            "known_params": {"issue": "Projector"}, "entities": {"issue": "Projector"},
            "missing_params": ["location", "floor"], "depends_on": [], "parallel_safe": False,
        },
        {
            "task_id": "t2", "intent": "CERTIFICATE", "summary": "Request a bonafide certificate",
            "known_params": {"certificate_type": "Bonafide certificate"},
            "entities": {"certificate_type": "Bonafide certificate"}, "missing_params": [],
            "depends_on": ["t1"], "parallel_safe": False,
        },
    ], "NORMAL", "test"))
    response = client.post(
        "/api/v1/assistant", json={"text": "Do the dependent tasks"}, headers=student_headers,
    ).json()
    by_id = {item["task_id"]: item for item in response["outputs"]}
    assert by_id["t1"]["task_status"] == "PENDING"
    assert by_id["t2"]["task_status"] == "PENDING"
    assert by_id["t2"]["depends_on"] == ["t1"]


def test_meaning_based_fallback_finds_back_to_back_tasks_without_connectors(monkeypatch):
    monkeypatch.setattr("app.agents.gemini_adapter.plan", lambda _text, _plan=None: None)
    tasks, _, _ = propose_plan(
        "Tell me the hostel policy report the broken AC in Building LH room 123 ground floor"
    )
    assert [task["intent"] for task in tasks] == ["POLICY_QUESTION", "MAINTENANCE"]
    assert all(task["depends_on"] == [] for task in tasks)


def test_connector_inside_one_recurring_booking_does_not_split_task(monkeypatch):
    monkeypatch.setattr("app.agents.gemini_adapter.plan", lambda _text, _plan=None: None)
    tasks, _, _ = propose_plan("Book the lab for Friday and Saturday")
    assert len(tasks) == 1
    assert tasks[0]["intent"] == "LAB_BOOKING"


def test_follow_up_updates_existing_task_id_and_returns_full_plan(monkeypatch):
    monkeypatch.setattr("app.agents.gemini_adapter.plan", lambda _text, _plan=None: None)
    current = [
        {
            "task_id": "t1", "intent": "MAINTENANCE", "summary": "Broken AC in Building LH room 123",
            "known_params": {"issue": "Air conditioner", "location": "Room 123, Building LH"},
            "entities": {"issue": "Air conditioner", "location": "Room 123, Building LH"},
            "missing_params": ["floor"], "depends_on": [], "parallel_safe": True, "status": "WAITING",
        },
        {
            "task_id": "t2", "intent": "POLICY_QUESTION", "summary": "Hostel policy",
            "known_params": {"policy_topic": "hostel"}, "entities": {"policy_topic": "hostel"},
            "missing_params": [], "depends_on": [], "parallel_safe": True, "status": "COMPLETED",
        },
    ]
    tasks, _, source = propose_plan("second floor", current)
    assert source == "deterministic-update"
    assert len(tasks) == 2
    assert tasks[0]["task_id"] == "t1"
    assert tasks[0]["known_params"]["floor"] == "2"
    assert tasks[0]["missing_params"] == []
    assert tasks[1]["status"] == "COMPLETED"


def test_frustration_applies_defaults_and_requires_human_confirmation(monkeypatch):
    monkeypatch.setattr("app.agents.gemini_adapter.plan", lambda _text, _plan=None: None)
    current = [{
        "task_id": "t1", "intent": "MAINTENANCE", "summary": "Broken projector",
        "known_params": {"issue": "Projector"}, "entities": {"issue": "Projector"},
        "missing_params": ["location", "floor"], "depends_on": [], "parallel_safe": True,
        "status": "WAITING",
    }]
    tasks, _, _ = propose_plan("whatever, stop asking", current, frustrated=True)
    assert tasks[0]["missing_params"] == []
    assert tasks[0]["requires_human_confirmation"] is True
    assert set(tasks[0]["defaults_applied"]) == {"location", "floor"}


def test_parallel_safety_is_false_only_for_competing_resource_or_record():
    tasks = [
        {"task_id": "t1", "intent": "LAB_BOOKING", "known_params": {"space": "Library 203", "date": "2026-09-04"}},
        {"task_id": "t2", "intent": "LAB_BOOKING", "known_params": {"space": "Library 203", "date": "2026-09-04"}},
        {"task_id": "t3", "intent": "POLICY_QUESTION", "known_params": {"policy_topic": "hostel"}},
    ]
    normalized = _normalize_parallel_safety(tasks)
    assert normalized[0]["parallel_safe"] is False
    assert normalized[1]["parallel_safe"] is False
    assert normalized[2]["parallel_safe"] is True


def test_full_plan_and_results_survive_memory_cache_loss():
    owner = "persistence-test-owner"
    tasks = [{
        "task_id": "t1", "intent": "POLICY_QUESTION", "summary": "Read hostel policy",
        "known_params": {"policy_topic": "hostel"}, "missing_params": [],
        "depends_on": [], "parallel_safe": True, "status": "COMPLETED",
        "result": [{"message": "Persisted answer"}],
    }]
    with SessionLocal() as db:
        saved = task_plan_service.save(db, owner, tasks)
        plan_id = saved.id
    with SessionLocal() as fresh_db:
        reloaded = fresh_db.get(type(saved), plan_id)
        assert task_plan_service.tasks(reloaded) == tasks


def test_idempotency_returns_cached_result_without_reexecuting(monkeypatch):
    calls = 0
    owner = "idempotency-test-owner"
    task = {
        "task_id": "t1", "intent": "POLICY_QUESTION", "summary": "Read policy",
        "known_params": {"policy_topic": "hostel"}, "missing_params": [],
        "depends_on": [], "parallel_safe": True,
    }
    with SessionLocal() as db:
        plan_id = task_plan_service.save(db, owner, [task]).id

    def fake_execute(*_args):
        nonlocal calls
        calls += 1
        return [{"message": "one execution"}]

    monkeypatch.setattr("app.api.routes._execute_compound_task", fake_execute)
    first = _run_task_once(plan_id, dict(task), owner, None, "en")
    second = _run_task_once(plan_id, dict(task), owner, None, "en")
    assert calls == 1
    assert first[1] == second[1] == "COMPLETED"
    assert second[2] is True
    assert second[0][0]["cache_hit"] is True


def test_parallel_safe_tasks_overlap(monkeypatch):
    active = 0
    peak = 0

    def fake_run(_plan_id, task, *_args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.05)
        active -= 1
        return ([{"task_id": task["task_id"], "task_status": "COMPLETED"}], "COMPLETED", False)

    monkeypatch.setattr("app.api.routes._run_task_once", fake_run)
    tasks = [
        {"task_id": "t1", "missing_params": [], "depends_on": [], "parallel_safe": True},
        {"task_id": "t2", "missing_params": [], "depends_on": [], "parallel_safe": True},
    ]
    _outputs, groups = asyncio.run(_execute_task_plan_async(tasks, "PLAN-P", "u", None, "en"))
    assert peak == 2
    assert groups == [["t1", "t2"]]


def test_parallel_unsafe_tasks_execute_sequentially(monkeypatch):
    order = []

    def fake_run(_plan_id, task, *_args):
        order.append(task["task_id"])
        return ([{"task_id": task["task_id"], "task_status": "COMPLETED"}], "COMPLETED", False)

    monkeypatch.setattr("app.api.routes._run_task_once", fake_run)
    tasks = [
        {"task_id": "t1", "missing_params": [], "depends_on": [], "parallel_safe": False},
        {"task_id": "t2", "missing_params": [], "depends_on": [], "parallel_safe": False},
    ]
    _outputs, groups = asyncio.run(_execute_task_plan_async(tasks, "PLAN-S", "u", None, "en"))
    assert order == ["t1", "t2"]
    assert groups == [["t1"], ["t2"]]


def test_failed_dependency_compensates_only_its_own_side_effect(monkeypatch):
    compensated = []

    def fake_run(_plan_id, task, *_args):
        if task["task_id"] == "t1":
            return ([{"decision": {"request_id": "REQ-1", "intent": "MAINTENANCE"}}], "FAILED", False)
        return ([{"task_id": task["task_id"], "task_status": "COMPLETED"}], "COMPLETED", False)

    monkeypatch.setattr("app.api.routes._run_task_once", fake_run)
    monkeypatch.setattr("app.api.routes.request_service.compensate", lambda ref: compensated.append(ref) or True)
    monkeypatch.setattr("app.api.routes.task_plan_service.mark_compensated", lambda *_args: None)
    tasks = [
        {"task_id": "t1", "missing_params": [], "depends_on": [], "parallel_safe": True},
        {"task_id": "t2", "missing_params": [], "depends_on": ["t1"], "parallel_safe": True},
        {"task_id": "t3", "missing_params": [], "depends_on": [], "parallel_safe": True},
    ]
    asyncio.run(_execute_task_plan_async(tasks, "PLAN-C", "u", None, "en"))
    states = {task["task_id"]: task["status"] for task in tasks}
    assert compensated == ["REQ-1"]
    assert states == {"t1": "ROLLED_BACK", "t2": "FAILED", "t3": "COMPLETED"}
