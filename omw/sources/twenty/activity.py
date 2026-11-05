"""Notes, tasks and their targets (the company, person or opportunity each one is attached to)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.columns import column
from omw.sources.twenty.shapes import SOURCE, _ts


def _body(obj: Mapping[str, Any]) -> str:
    body = obj.get("bodyV2")
    if isinstance(body, Mapping):
        return str(body.get("markdown") or "")
    return str(obj.get("body") or "")


def _actor_name(obj: Mapping[str, Any]) -> str:
    actor = obj.get("createdBy")
    return str(actor.get("name") or "") if isinstance(actor, Mapping) else ""


def shape_note(obj: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "title": str(obj.get("title") or ""),
        "body": _body(obj),
        "created_by": _actor_name(obj),
        "created_at": _ts(obj.get("createdAt")),
        "updated_at": _ts(obj.get("updatedAt")),
        "deleted_at": _ts(obj.get("deletedAt")),
    }


NOTE_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "title": column("text"),
    "body": column("text"),
    "created_by": column("text"),
    "created_at": column("timestamp"),
    "updated_at": column("timestamp"),
    "deleted_at": column("timestamp"),
}


def shape_task(obj: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "title": str(obj.get("title") or ""),
        "body": _body(obj),
        "status": str(obj.get("status") or ""),
        "due_at": _ts(obj.get("dueAt")),
        "assignee_id": str(obj.get("assigneeId") or ""),
        "created_by": _actor_name(obj),
        "created_at": _ts(obj.get("createdAt")),
        "updated_at": _ts(obj.get("updatedAt")),
        "deleted_at": _ts(obj.get("deletedAt")),
    }


TASK_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "title": column("text"),
    "body": column("text"),
    "status": column("text"),
    "due_at": column("timestamp"),
    "assignee_id": column("text"),
    "created_by": column("text"),
    "created_at": column("timestamp"),
    "updated_at": column("timestamp"),
    "deleted_at": column("timestamp"),
}


def _shape_target(obj: Mapping[str, Any], owner_field: str, owner_column: str) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        owner_column: str(obj.get(owner_field) or ""),
        "person_id": str(obj.get("personId") or ""),
        "company_id": str(obj.get("companyId") or ""),
        "opportunity_id": str(obj.get("opportunityId") or ""),
        "created_at": _ts(obj.get("createdAt")),
        "updated_at": _ts(obj.get("updatedAt")),
        "deleted_at": _ts(obj.get("deletedAt")),
    }


def shape_note_target(obj: Mapping[str, Any]) -> dict[str, Any]:
    return _shape_target(obj, "noteId", "note_id")


def shape_task_target(obj: Mapping[str, Any]) -> dict[str, Any]:
    return _shape_target(obj, "taskId", "task_id")


def _target_columns(owner_column: str) -> dict[str, TColumnSchema]:
    return {
        "source": column("text"),
        "id": column("text"),
        owner_column: column("text"),
        "person_id": column("text"),
        "company_id": column("text"),
        "opportunity_id": column("text"),
        "created_at": column("timestamp"),
        "updated_at": column("timestamp"),
        "deleted_at": column("timestamp"),
    }


NOTE_TARGET_COLUMNS = _target_columns("note_id")
TASK_TARGET_COLUMNS = _target_columns("task_id")
