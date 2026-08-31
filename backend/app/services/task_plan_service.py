"""Durable plans and exactly-once task result lookup."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db_models import TaskExecutionRecord, TaskPlanRecord

TERMINAL_TASK_STATES = {"COMPLETED", "FAILED", "ROLLED_BACK"}


def load_active(db: Session, owner_key: str) -> TaskPlanRecord | None:
    return (db.query(TaskPlanRecord)
            .filter(TaskPlanRecord.owner_key == owner_key, TaskPlanRecord.status == "ACTIVE")
            .order_by(TaskPlanRecord.updated_at.desc()).first())


def tasks(record: TaskPlanRecord | None) -> list[dict]:
    if not record:
        return []
    try:
        value = json.loads(record.plan_json)
        return value if isinstance(value, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def save(db: Session, owner_key: str, plan_tasks: list[dict], plan_id: str | None = None) -> TaskPlanRecord:
    record = db.get(TaskPlanRecord, plan_id) if plan_id else load_active(db, owner_key)
    if not record:
        record = TaskPlanRecord(id=f"PLAN-{uuid4().hex[:12].upper()}", owner_key=owner_key)
        db.add(record)
    record.plan_json = json.dumps(plan_tasks, ensure_ascii=False, default=str)
    states = {task.get("status", "PENDING") for task in plan_tasks}
    if "FAILED" in states:
        record.status = "FAILED"
    elif states and states.issubset(TERMINAL_TASK_STATES):
        record.status = "COMPLETED"
    else:
        record.status = "ACTIVE"
    record.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    return record


def cached(db: Session, plan_id: str, task_id: str) -> tuple[list[dict], str] | None:
    row = (db.query(TaskExecutionRecord)
           .filter(TaskExecutionRecord.plan_id == plan_id, TaskExecutionRecord.task_id == task_id).first())
    if not row or row.status not in TERMINAL_TASK_STATES | {"AWAITING_CONFIRMATION"}:
        return None
    try:
        result = json.loads(row.result_json)
    except json.JSONDecodeError:
        result = []
    return result, row.status


def execution(db: Session, plan_id: str, task_id: str) -> TaskExecutionRecord | None:
    return (db.query(TaskExecutionRecord)
            .filter(TaskExecutionRecord.plan_id == plan_id, TaskExecutionRecord.task_id == task_id).first())


def record_result(db: Session, plan_id: str, task_id: str, status: str, result: list[dict],
                  side_effect_type: str = "", side_effect_ref: str = "") -> TaskExecutionRecord:
    row = (db.query(TaskExecutionRecord)
           .filter(TaskExecutionRecord.plan_id == plan_id, TaskExecutionRecord.task_id == task_id).first())
    if not row:
        row = TaskExecutionRecord(plan_id=plan_id, task_id=task_id, status=status)
        db.add(row)
    row.status = status
    row.result_json = json.dumps(result, ensure_ascii=False, default=str)
    row.side_effect_type = side_effect_type
    row.side_effect_ref = side_effect_ref
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def mark_compensated(db: Session, plan_id: str, task_id: str, success: bool) -> None:
    row = (db.query(TaskExecutionRecord)
           .filter(TaskExecutionRecord.plan_id == plan_id, TaskExecutionRecord.task_id == task_id).first())
    if not row:
        return
    row.compensation_status = "ROLLED_BACK" if success else "ROLLBACK_FAILED"
    if success:
        row.status = "ROLLED_BACK"
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
