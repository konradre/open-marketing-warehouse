"""Timeline activities: Twenty's own change feed (a record was created, updated, deleted or restored, a
note or task was linked to it), one row per event.

Twenty has shipped two shapes of this object and a self-hosted instance may run either:

- the event name in ``name`` (``opportunity.updated``, ``linked-note.created``) and the record it happened
  on in ``personId`` / ``companyId`` / ``opportunityId`` / ``noteId`` / ``taskId``;
- the event type in ``timelineActivityTypeSnapshot`` (``name``, ``action``, ``label``) and the record in
  ``targetPersonId`` / ``targetCompanyId`` / ``targetOpportunityId`` / ``targetNoteId`` / ``targetTaskId``.

``shape_timeline_activity`` reads both. ``properties`` (the before and after values of an update) stays in
``raw`` as JSON: it can carry anything a record holds. Only the names of the changed fields and the stage
move of an opportunity are lifted into columns of their own.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from dlt.common.schema.typing import TColumnSchema

from omw.sources.columns import column
from omw.sources.twenty.shapes import SOURCE, _ts

LINKED_PREFIX = "linked-"
TARGETS: dict[str, str] = {
    "person_id": "Person",
    "company_id": "Company",
    "opportunity_id": "Opportunity",
    "note_id": "Note",
    "task_id": "Task",
}
"""Warehouse column -> the record kind in Twenty's field names (``targetPersonId`` or ``personId``)."""


def _mapping(value: Any) -> Mapping[str, Any]:
    """A JSON object field, which the API returns as an object and older versions as a string."""
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    """A before or after value as text: stage names are strings, anything else is not a stage."""
    return value if isinstance(value, str) else ""


def _target(obj: Mapping[str, Any], kind: str) -> str:
    return str(obj.get(f"target{kind}Id") or obj.get(f"{kind[0].lower()}{kind[1:]}Id") or "")


def shape_timeline_activity(obj: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _mapping(obj.get("timelineActivityTypeSnapshot"))
    event = str(obj.get("name") or snapshot.get("name") or "")
    subject, dot, verb = event.removeprefix(LINKED_PREFIX).rpartition(".")
    properties = _mapping(obj.get("properties"))
    diff = _mapping(properties.get("diff"))
    stage = _mapping(diff.get("stage"))
    return {
        "source": SOURCE,
        "id": str(obj["id"]),
        "happens_at": _ts(obj.get("happensAt") or obj.get("createdAt")),
        "event": event,
        "object_name": subject if dot else "",
        "action": str(snapshot.get("action") or (verb if dot else "")),
        "is_linked": event.startswith(LINKED_PREFIX),
        "changed_fields": ",".join(sorted(str(field) for field in diff)),
        "stage_before": _text(stage.get("before")),
        "stage_after": _text(stage.get("after")),
        "linked_record_id": str(obj.get("linkedRecordId") or ""),
        "linked_record_name": str(obj.get("linkedRecordCachedName") or ""),
        "member_id": str(obj.get("workspaceMemberId") or ""),
        **{name: _target(obj, kind) for name, kind in TARGETS.items()},
        "properties": json.dumps(properties, sort_keys=True, default=str) if properties else "",
        "created_at": _ts(obj.get("createdAt")),
        "updated_at": _ts(obj.get("updatedAt")),
        "deleted_at": _ts(obj.get("deletedAt")),
    }


TIMELINE_ACTIVITY_COLUMNS: dict[str, TColumnSchema] = {
    "source": column("text"),
    "id": column("text"),
    "happens_at": column("timestamp"),
    "event": column("text"),
    "object_name": column("text"),
    "action": column("text"),
    "is_linked": column("bool"),
    "changed_fields": column("text"),
    "stage_before": column("text"),
    "stage_after": column("text"),
    "linked_record_id": column("text"),
    "linked_record_name": column("text"),
    "member_id": column("text"),
    **{name: column("text") for name in TARGETS},
    "properties": column("text"),
    "created_at": column("timestamp"),
    "updated_at": column("timestamp"),
    "deleted_at": column("timestamp"),
}
