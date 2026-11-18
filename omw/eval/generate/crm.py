"""Twenty CRM (companies, people, opportunities, notes and tasks) and the lead-ad submissions of Meta and
LinkedIn.

Opportunity attribution resolves opportunity, then contact, then company, field by field, as the view does.
Notes and tasks are attached to one or two records, so the activity grain has more rows than activities.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from omw.eval.generate.campaigns import CAMPAIGNS, LINKEDIN_ACCOUNT, META_ACCOUNT
from omw.eval.generate.linkedin import linkedin_creatives
from omw.eval.generate.rng import Rng
from omw.eval.generate.stripe import EPOCH, FIELDS, NONE, attribution, resolve
from omw.eval.world import Row, World, days

ADJECTIVES = (
    "Amber",
    "Boreal",
    "Cobalt",
    "Delta",
    "Ember",
    "Fathom",
    "Granite",
    "Harbor",
    "Indigo",
    "Juniper",
)
NOUNS = ("Robotics", "Logistics", "Foods", "Labs", "Freight")
FIRST = ("Ada", "Ben", "Chloe", "Dev", "Elif", "Farah", "Goran", "Hana", "Ivo", "Jun")
LAST = ("Arden", "Baptiste", "Castell", "Dimov", "Eze", "Fontaine", "Grieve")
STAGES = (("NEW", 0.2), ("SCREENING", 0.2), ("MEETING", 0.2), ("PROPOSAL", 0.15), ("CUSTOMER", 0.25))
CRM_KEYS = (
    ("widgets_prospecting", 0.3),
    ("gadgets_launch", 0.2),
    ("abm_tier_1", 0.2),
    ("lead_gen_ebook", 0.15),
    ("brand_search", 0.1),
    ("webinar", 0.05),
)
MEMBERS = ("member-ana", "member-bo", "member-cy")


def _uuid(kind: int, number: int) -> str:
    return f"00000000-0000-4000-8{kind:03d}-{number:012d}"


def _attribution(rng: Rng, probability: float, *keys: object) -> tuple[str, str, str, str]:
    if not rng.chance(probability, *keys, "attributed"):
        return NONE
    key = rng.weighted(CRM_KEYS, *keys, "key")
    return ("", "webinar", "event", "q3_webinar") if key == "webinar" else attribution(key)


def _stamps(created: datetime, deleted: bool = False) -> Row:
    return {
        "created_at": created,
        "updated_at": created + timedelta(days=1),
        "deleted_at": created + timedelta(days=3) if deleted else EPOCH,
    }


def build_twenty(rng: Rng, world: World) -> None:
    companies: dict[str, Row] = {}
    for n in range(50):
        created = datetime(2026, 2, 1, 9, 0) + timedelta(days=n * 4)
        name = f"{ADJECTIVES[n % 10]} {NOUNS[n // 10]}"
        row = {
            "source": "twenty",
            "id": _uuid(1, n),
            "name": name,
            "domain": f"{name.lower().replace(' ', '-')}.example",
            "employees": 20 + n * 7,
            "annual_recurring_revenue": Decimal(0),
            "currency": "USD",
            "ideal_customer_profile": n % 3 == 0,
            "account_owner_id": MEMBERS[n % 3],
            **_stamps(created),
            **dict(zip(FIELDS, _attribution(rng, 0.3, "company", n), strict=True)),
        }
        companies[row["id"]] = row
    people: dict[str, Row] = {}
    for n in range(70):
        created = datetime(2026, 2, 10, 11, 0) + timedelta(days=n * 3)
        row = {
            "source": "twenty",
            "id": _uuid(2, n),
            "first_name": FIRST[n % 10],
            "last_name": LAST[n % 7],
            "email": f"contact{n}@prospects.example",
            "job_title": "Operations Lead",
            "city": "Springfield",
            "company_id": _uuid(1, n % 50),
            **_stamps(created),
            **dict(zip(FIELDS, _attribution(rng, 0.3, "person", n), strict=True)),
        }
        people[row["id"]] = row
    world.add_raw("twenty___companies", companies.values())
    world.add_raw("twenty___people", people.values())

    opportunities: dict[str, Row] = {}
    number = 0
    for day in days():
        for slot in range(int(rng.between(0, 3.4, "twenty", day, "opportunities"))):
            key = ("opportunity", number)
            company = companies[_uuid(1, rng.integer(0, 49, *key, "company"))]
            contacts = [p for p in people.values() if p["company_id"] == company["id"]]
            person = rng.pick(contacts, *key, "person") if rng.chance(0.75, *key, "has_person") else None
            currency = rng.weighted((("USD", 0.8), ("EUR", 0.15), ("", 0.05)), *key, "currency")
            amount = rng.integer(4, 120, *key, "amount") * 500 if currency else 0
            created = datetime(day.year, day.month, day.day, rng.integer(8, 18, *key, "hour"), slot)
            own = _attribution(rng, 0.35, *key)
            row = {
                "source": "twenty",
                "id": _uuid(3, number),
                "name": f"{company['name']} - deal {number}",
                "stage": rng.weighted(STAGES, *key, "stage"),
                "amount": Decimal(amount),
                "currency": currency,
                "close_date": created + timedelta(days=45),
                "company_id": company["id"],
                "person_id": person["id"] if person else "",
                **_stamps(created, deleted=rng.chance(0.05, *key, "deleted")),
                **dict(zip(FIELDS, own, strict=True)),
            }
            opportunities[row["id"]] = row
            number += 1
            if row["deleted_at"] != EPOCH:
                continue
            levels = [own, _fields(person), _fields(company)]
            resolved = resolve(*levels)
            world.add(
                "twenty_pipeline",
                [
                    {
                        "source": "twenty",
                        "report": "opportunities",
                        "date": day,
                        "opportunity_id": row["id"],
                        "opportunity_name": row["name"],
                        "stage": row["stage"],
                        "is_won": row["stage"] == "CUSTOMER",
                        "is_lost": False,
                        "amount": row["amount"],
                        "currency": currency,
                        "company_id": company["id"],
                        "company_name": company["name"],
                        "person_id": row["person_id"],
                        **dict(zip(FIELDS, resolved, strict=True)),
                        "attributed": any(resolved),
                    }
                ],
            )
    world.add_raw("twenty___opportunities", opportunities.values())
    _activity(rng, world, companies, people, opportunities)


def _fields(record: Row | None) -> tuple[str, str, str, str]:
    if record is None:
        return NONE
    key, source, medium, campaign = (str(record[name]) for name in FIELDS)
    return (key, source, medium, campaign)


def _activity(
    rng: Rng, world: World, companies: dict[str, Row], people: dict[str, Row], opportunities: dict[str, Row]
) -> None:
    deals = list(opportunities.values())
    contacts = list(people.values())
    number = 0
    for day in days():
        for slot in range(int(rng.between(0, 3.2, "twenty", day, "activity"))):
            key = ("activity", number)
            kind = "task" if rng.chance(0.5, *key, "kind") else "note"
            created = datetime(day.year, day.month, day.day, rng.integer(8, 18, *key, "hour"), 30 + slot)
            deleted = rng.chance(0.05, *key, "deleted")
            activity_id = _uuid(4 if kind == "note" else 5, number)
            status = rng.weighted((("TODO", 0.4), ("IN_PROGRESS", 0.2), ("DONE", 0.4)), *key, "status")
            author = rng.pick(MEMBERS, *key, "author")
            base = {"source": "twenty", "id": activity_id, "title": f"{kind} {number}", "body": ""}
            base |= {"created_by": author, **_stamps(created, deleted)}
            if kind == "note":
                world.add_raw("twenty___notes", [base])
            else:
                task = {"status": status, "due_at": created + timedelta(days=7), "assignee_id": author}
                world.add_raw("twenty___tasks", [base | task])
            earlier = [d for d in deals if d["created_at"] < created]
            targets: list[tuple[str, str, str]] = []  # (person, company, opportunity)
            shape = rng.weighted(
                (("deal", 0.45), ("deal+person", 0.3), ("company", 0.2), ("none", 0.05)), *key
            )
            if shape.startswith("deal") and earlier:
                deal = rng.pick(earlier, *key, "deal")
                targets.append(("", "", deal["id"]))
                if shape == "deal+person":
                    targets.append((deal["person_id"] or rng.pick(contacts, *key, "person")["id"], "", ""))
            elif shape == "company":
                targets.append(("", _uuid(1, rng.integer(0, 49, *key, "company")), ""))
            for index, (person_id, company_id, opportunity_id) in enumerate(targets):
                target = {"source": "twenty", "id": _uuid(6, number * 10 + index), **_stamps(created)}
                target |= {"person_id": person_id, "company_id": company_id, "opportunity_id": opportunity_id}
                target["note_id" if kind == "note" else "task_id"] = activity_id
                world.add_raw(f"twenty___{kind}_targets", [target])
            number += 1
            if deleted:
                continue
            for person_id, company_id, opportunity_id in targets or [("", "", "")]:
                deal_row = opportunities.get(opportunity_id)
                person = people.get(person_id)
                company = companies.get(company_id)
                deal_company = companies.get(deal_row["company_id"]) if deal_row else None
                levels = [_fields(deal_row), _fields(person), _fields(company), _fields(deal_company)]
                world.add(
                    "twenty_activity",
                    [
                        {
                            "source": "twenty",
                            "report": "activity",
                            "date": day,
                            "activity_id": activity_id,
                            "kind": kind,
                            "status": status if kind == "task" else "",
                            "created_by": author,
                            "person_id": person_id,
                            "company_id": company_id or (deal_row["company_id"] if deal_row else ""),
                            "company_name": (company or deal_company or {"name": ""})["name"],
                            "opportunity_id": opportunity_id,
                            "opportunity_name": deal_row["name"] if deal_row else "",
                            "campaign_key": resolve(*levels)[0],
                        }
                    ],
                )


def build_leads(rng: Rng, world: World) -> None:
    meta = next(c for c in CAMPAIGNS if c.source == "meta_ads" and c.key == "lead_gen_ebook")
    tail = meta.campaign_id[-2:]
    number = 0
    for day in days():
        for slot in range(int(rng.between(0, 5.5, "meta_leads", day))):
            key = ("meta_lead", number)
            at = datetime(day.year, day.month, day.day, rng.integer(1, 21, *key, "hour"), slot)
            ad = rng.pick((("1", "Static image"), ("2", "Carousel")), *key, "ad")
            row = {
                "source": "meta_ads",
                "account_id": META_ACCOUNT,
                "date": day,
                "campaign_id": meta.campaign_id,
                "campaign_name": meta.name,
                "adset_id": f"2386000000000{tail}1",
                "adset_name": "Broad",
                "ad_id": f"2387000000000{tail}{ad[0]}",
                "ad_name": ad[1],
                "form_id": rng.pick(("5550001", "5550002"), *key, "form"),
                "is_organic": rng.chance(0.12, *key, "organic"),
                "platform": "ig" if rng.chance(0.35, *key, "platform") else "fb",
            }
            lead_id = f"77700000{number:06d}"
            world.add(
                "meta_ads_leads", [{**row, "report": "leads", "currency": meta.currency, "lead_id": lead_id}]
            )
            world.add_raw(
                "meta_ads___leads",
                [{**row, "id": lead_id, "created_time": at, "field_data": "[]", "field_count": 3}],
            )
            number += 1

    number = 0
    for campaign in (
        c for c in CAMPAIGNS if c.source == "linkedin_ads" and c.key in ("lead_gen_ebook", "abm_tier_1")
    ):
        rate = 2.6 if campaign.key == "lead_gen_ebook" else 1.3
        creatives = [creative_id for creative_id, *_ in linkedin_creatives(campaign)]
        for day in days():
            for slot in range(int(rng.between(0, rate, "linkedin_leads", campaign.campaign_id, day))):
                key = ("linkedin_lead", number)
                form = rng.pick(("660001", "660002"), *key, "form")
                row = {
                    "source": "linkedin_ads",
                    "account_id": LINKEDIN_ACCOUNT,
                    "date": day,
                    "campaign_id": campaign.campaign_id,
                    "creative_id": rng.pick(creatives, *key, "creative"),
                    "form_id": form,
                    "lead_type": "ORGANIC" if rng.chance(0.1, *key, "organic") else "SPONSORED",
                }
                lead_id = f"lead-{number:06d}"
                at = datetime(day.year, day.month, day.day, rng.integer(1, 21, *key, "hour"), slot)
                world.add(
                    "linkedin_ads_leads",
                    [
                        {
                            **row,
                            "report": "leads",
                            "currency": campaign.currency,
                            "campaign_name": campaign.name,
                            "lead_id": lead_id,
                        }
                    ],
                )
                world.add_raw(
                    "linkedin_ads___lead_form_responses",
                    [
                        {
                            **row,
                            "id": lead_id,
                            "submitted_at": at,
                            "form_urn": f"urn:li:leadGenForm:{form}",
                            "answers": "[]",
                            "answer_count": 4,
                        }
                    ],
                )
                number += 1
