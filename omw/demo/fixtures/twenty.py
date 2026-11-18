"""Twenty CRM as its REST API answers: companies, people, opportunities, notes, tasks, their targets and
the timeline (the change feed).

Each record is shown in the state it had on the morning of the snapshot: an opportunity sits at the stage
it had reached, with ``updatedAt`` at its last move, so the pipeline's ``updatedAt`` cursor picks up exactly
the records that moved between two snapshots. Attribution (``campaignKey`` and the ``utm`` fields) sits on
the person or on the company, never on the opportunity; the marts resolve it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from omw.demo.fixtures.common import Payloads, iso_z, uuid
from omw.demo.funnel import Deal, Funnel
from omw.demo.world import instant

MEMBERS = tuple(uuid("member", n) for n in range(3))
TITLES = ("Head of Operations", "Procurement Lead", "COO", "IT Manager", "Founder")
CITIES = ("Austin", "Berlin", "Toronto", "Leeds", "Lyon", "Denver")
BLANK = {"campaignKey": "", "utmSource": "", "utmMedium": "", "utmCampaign": ""}


def _attribution(deal: Deal, on: str) -> dict[str, str]:
    if not deal.key or deal.attribution_on != on:
        return dict(BLANK)
    return {
        "campaignKey": deal.key,
        "utmSource": deal.utm_source,
        "utmMedium": deal.utm_medium,
        "utmCampaign": deal.key,
    }


def _stamps(
    created: datetime, updated: datetime | None = None, deleted: datetime | None = None
) -> dict[str, Any]:
    return {"createdAt": iso_z(created), "updatedAt": iso_z(updated or created), "deletedAt": iso_z(deleted)}


def _listing(path: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every record of an object, live and deleted; the replay filters, orders and pages it."""
    return {
        "data": {path: records},
        "pageInfo": {"hasNextPage": False, "startCursor": None, "endCursor": None},
        "totalCount": len(records),
    }


def _event(
    kind: str, number: int, at: datetime, name: str, properties: dict[str, Any], **target: Any
) -> dict[str, Any]:
    targets = dict.fromkeys(("personId", "companyId", "opportunityId", "noteId", "taskId"))
    return {
        "id": uuid("timeline", kind, number, at.isoformat()),
        "happensAt": iso_z(at),
        "name": name,
        "properties": properties,
        "linkedRecordCachedName": target.pop("linked_name", None),
        "linkedRecordId": target.pop("linked_id", None),
        "linkedObjectMetadataId": None,
        "workspaceMemberId": MEMBERS[number % len(MEMBERS)],
        **targets,
        **target,
        **_stamps(at),
    }


def build(funnel: Funnel, as_of: date) -> Payloads:
    now = instant(as_of)
    records: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "companies",
            "people",
            "opportunities",
            "notes",
            "tasks",
            "noteTargets",
            "taskTargets",
            "timelineActivities",
        )  # fmt: skip
    }
    for deal in funnel.deals:
        if deal.person_at >= now:
            continue
        number = deal.number
        company_id, person_id, opportunity_id = (uuid(kind, number) for kind in ("company", "person", "opp"))
        won = deal.won_at if deal.won_at is not None and deal.won_at < now else None
        records["companies"].append(
            {
                "id": company_id,
                "name": deal.company,
                "domainName": {"primaryLinkUrl": deal.domain, "primaryLinkLabel": ""},
                "employees": deal.seats * 4,
                "annualRecurringRevenue": {
                    "amountMicros": deal.annual_micros if won else None,
                    "currencyCode": deal.currency,
                },
                "idealCustomerProfile": deal.seats >= 50,
                "accountOwnerId": MEMBERS[number % len(MEMBERS)],
                **_stamps(deal.person_at, won),
                **_attribution(deal, "company"),
            }
        )
        records["people"].append(
            {
                "id": person_id,
                "name": {"firstName": deal.first_name, "lastName": deal.last_name},
                "emails": {
                    "primaryEmail": f"{deal.first_name}.{deal.last_name}@{deal.domain}".lower(),
                    "additionalEmails": [],
                },
                "jobTitle": TITLES[number % len(TITLES)],
                "city": CITIES[number % len(CITIES)],
                "companyId": company_id,
                **_stamps(deal.person_at + timedelta(minutes=2)),
                **_attribution(deal, "person"),
            }
        )
        if deal.opened_at is None or deal.opened_at >= now:
            continue
        moved, stage = deal.stage_at(now) or (deal.opened_at, "NEW")
        removed = deal.removed_at if deal.removed_at is not None and deal.removed_at < now else None
        title = f"{deal.company} - {deal.seats} seats"
        records["opportunities"].append(
            {
                "id": opportunity_id,
                "name": title,
                "stage": stage,
                "amount": {"amountMicros": deal.annual_micros, "currencyCode": deal.currency},
                "closeDate": iso_z(won or deal.opened_at + timedelta(days=45)),
                "companyId": company_id,
                "pointOfContactId": person_id,
                **_stamps(deal.opened_at, removed or moved, removed),
                **BLANK,
            }
        )
        feed = records["timelineActivities"]
        created = {"after": {"name": title, "stage": "NEW"}}
        feed.append(
            _event(
                "opp", number, deal.opened_at, "opportunity.created", created, opportunityId=opportunity_id
            )
        )
        if removed is not None:
            feed.append(
                _event("opp", number, removed, "opportunity.deleted", {}, opportunityId=opportunity_id)
            )
        before = "NEW"
        for index, (when, reached) in enumerate(deal.stages[1:]):
            if when >= now:
                break
            diff = {"diff": {"stage": {"before": before, "after": reached}}}
            feed.append(
                _event("opp", number, when, "opportunity.updated", diff, opportunityId=opportunity_id)
            )
            before = reached
            later = deal.stages[index + 2][0] if index + 2 < len(deal.stages) else None
            if reached == "MEETING":
                _note(records, number, when + timedelta(hours=1), opportunity_id, company_id)
            if reached == "PROPOSAL":
                _task(records, number, when + timedelta(minutes=30), opportunity_id, later, now)
    return {_file(path): _listing(path, found) for path, found in records.items()}


def _file(path: str) -> str:
    return {
        "noteTargets": "note_targets",
        "taskTargets": "task_targets",
        "timelineActivities": "timeline_activities",
    }.get(path, path)


def _note(
    records: dict[str, list[dict[str, Any]]], number: int, at: datetime, opportunity_id: str, company_id: str
) -> None:
    note_id = uuid("note", number)
    records["notes"].append(
        {
            "id": note_id,
            "title": "Discovery call",
            "bodyV2": {
                "markdown": "Walked through the current process and the seats needed.",
                "blocknote": "[]",
            },
            "createdBy": {"source": "MANUAL", "name": f"Rep {number % len(MEMBERS) + 1}"},
            **_stamps(at),
        }
    )
    for kind, target in (("opportunityId", opportunity_id), ("companyId", company_id)):
        blank = dict.fromkeys(("personId", "companyId", "opportunityId"))
        records["noteTargets"].append(
            {"id": uuid("note-target", number, kind), "noteId": note_id, **blank, kind: target, **_stamps(at)}
        )
    records["timelineActivities"].append(
        _event(
            "note",
            number,
            at,
            "linked-note.created",
            {},
            opportunityId=opportunity_id,
            linked_name="Discovery call",
            linked_id=note_id,
        )  # fmt: skip
    )


def _task(
    records: dict[str, list[dict[str, Any]]],
    number: int,
    at: datetime,
    opportunity_id: str,
    next_move: datetime | None,
    now: datetime,
) -> None:
    task_id = uuid("task", number)
    done = next_move if next_move is not None and next_move < now else None
    records["tasks"].append(
        {
            "id": task_id,
            "title": "Send proposal",
            "bodyV2": {"markdown": "", "blocknote": "[]"},
            "status": "DONE" if done else "TODO",
            "dueAt": iso_z(at + timedelta(days=3)),
            "assigneeId": MEMBERS[number % len(MEMBERS)],
            "createdBy": {"source": "MANUAL", "name": f"Rep {number % len(MEMBERS) + 1}"},
            **_stamps(at, done),
        }
    )
    blank = dict.fromkeys(("personId", "companyId"))
    records["taskTargets"].append(
        {
            "id": uuid("task-target", number),
            "taskId": task_id,
            **blank,
            "opportunityId": opportunity_id,
            **_stamps(at),
        }
    )
