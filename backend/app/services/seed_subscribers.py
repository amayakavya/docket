"""
Deterministic subscriber fixtures.

The earlier revision of this file carried every record by hand, which made it long
and impossible to keep internally consistent. Records are generated here instead,
from a fixed seed, so the same database comes out of every run and a reviewer can
read the shape of a subscriber in forty lines rather than two thousand.

Contact details are deliberately unroutable: telephone numbers begin 55, which is
not an allocated mobile series, and every address is at example.com.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import Subscriber

SEED = 4703
COUNT = 24

_FIRST = ["Asha", "Nikhil", "Farida", "Joel", "Meera", "Tarun", "Ivy", "Rohan",
          "Sana", "Devika", "Omar", "Ritu", "Kabir", "Leena", "Vikram", "Anaya"]
_LAST = ["Menon", "Bose", "Qureshi", "D'Souza", "Iyer", "Grewal", "Thomas",
         "Naidu", "Pillai", "Chatterjee", "Rao", "Fernandes"]

_AREAS = [("Fort Exchange", "EX-001"), ("Andheri Exchange", "EX-014"),
          ("Whitefield Exchange", "EX-032"), ("Salt Lake Exchange", "EX-047"),
          ("Gachibowli Exchange", "EX-058"), ("Kochi Exchange", "EX-066")]

_SEGMENTS = ["RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL", "BUSINESS",
             "ENTERPRISE", "PRIORITY", "STUDENT"]

_LINK_TYPES = [("FIBRE", [100, 200, 300, 500, 1000]),
               ("DSL", [10, 20, 40]),
               ("MOBILE", [0]),
               ("LEASED_LINE", [100, 200, 500])]

_DEVICES = [("ROUTER", "WR-820N"), ("ONT", "GP-1200"),
            ("SET_TOP_BOX", "STB-4K-II"), ("SIM", "eSIM-v3")]

_ADDONS = [("STATIC_IP", 250.0), ("OTT_BUNDLE", 199.0), ("EXTRA_DATA", 150.0),
           ("LANDLINE", 99.0), ("CLOUD_BACKUP", 120.0)]

_OCCUPATIONS = ["Teacher", "Architect", "Pharmacist", "Logistics Manager",
                "Chef", "Civil Engineer", "Journalist", "Physiotherapist"]


def _build(rng: random.Random, n: int) -> dict:
    first, last = rng.choice(_FIRST), rng.choice(_LAST)
    name = f"{first} {last}"
    handle = f"{first.lower()}.{last.lower().replace(chr(39), '')}"
    ref = f"CR{500000 + n * 37}"
    mobile = f"55{10000000 + n * 131}"
    area, area_code = rng.choice(_AREAS)
    segment = rng.choice(_SEGMENTS)
    opened = date(2016, 1, 1) + timedelta(days=rng.randrange(0, 3200))

    links, link_ids = [], []
    for i in range(rng.randint(1, 3)):
        kind, speeds = rng.choice(_LINK_TYPES)
        cid = f"{area_code}-{100000 + n * 17 + i}"
        link_ids.append(cid)
        links.append({
            "connection_id": cid, "type": kind,
            "plan": f"{kind.title()} {rng.choice(speeds)}" if speeds != [0] else "Mobile Unlimited",
            "speed_mbps": rng.choice(speeds),
            "monthly_rental": float(rng.randrange(399, 4999, 100)),
            "status": rng.choice(["ACTIVE", "ACTIVE", "ACTIVE", "SUSPENDED"]),
            "exchange_code": area_code, "service_area": area,
            "activated_on": (opened + timedelta(days=i * 90)).isoformat(),
        })

    devices, serials = [], []
    for i in range(rng.randint(1, 2)):
        kind, model = rng.choice(_DEVICES)
        sn = f"{kind[:3]}{900000 + n * 23 + i}"
        serials.append(sn)
        devices.append({
            "serial": sn, "type": kind, "model": model,
            "status": rng.choice(["ACTIVE", "ACTIVE", "FAULTY", "RETURNED"]),
            "issued_on": opened.isoformat(),
            "warranty_until": (opened + timedelta(days=730)).isoformat(),
            "linked_connection": link_ids[0],
        })

    contracts = []
    if rng.random() < 0.55:
        total = float(rng.randrange(6000, 48000, 1000))
        months = rng.choice([6, 12, 18, 24])
        contracts.append({
            "contract_id": f"CT{700000 + n * 11}",
            "type": rng.choice(["DEVICE_INSTALMENT", "ANNUAL_PLAN", "BUNDLE_LOCK_IN"]),
            "total_amount": total, "outstanding_amount": round(total * rng.random(), 2),
            "instalment_amount": round(total / months, 2), "tenure_months": months,
            "status": rng.choice(["ACTIVE", "ACTIVE", "CLOSED", "IN_ARREARS"]),
            "next_due_on": (date.today() + timedelta(days=rng.randrange(1, 30))).isoformat(),
        })

    addons = []
    for kind, charge in rng.sample(_ADDONS, rng.randint(0, 3)):
        addons.append({
            "addon_id": f"AD{300000 + n * 7 + len(addons)}", "type": kind,
            "monthly_charge": charge, "status": "ACTIVE",
            "renews_on": (date.today() + timedelta(days=rng.randrange(5, 300))).isoformat(),
        })

    roaming = None
    if rng.random() < 0.2:
        roaming = {
            "country": rng.choice(["United Kingdom", "Singapore", "Canada", "Germany"]),
            "pack": rng.choice(["Traveller 7", "Traveller 30", "Business Global"]),
            "activated_on": (date.today() - timedelta(days=rng.randrange(1, 200))).isoformat(),
            "data_allowance_gb": rng.choice([2, 5, 10]),
        }

    return {
        "customer_ref": ref, "name": name,
        "mobile_number": mobile, "email": f"{handle}@example.com",
        "tax_id": f"TX{rng.randrange(10**7, 10**8)}", "id_last4": f"XXXX {rng.randrange(1000, 9999)}",
        "date_of_birth": (date(1970, 1, 1) + timedelta(days=rng.randrange(0, 12000))).isoformat(),
        "service_area": area, "area_code": area_code,
        "status": "ACTIVE", "verification_status": rng.choice(["COMPLETED", "COMPLETED", "PENDING"]),
        "customer_segment": segment, "occupation": rng.choice(_OCCUPATIONS),
        "annual_income": float(rng.randrange(300000, 3000000, 50000)),
        "payment_score": rng.randrange(520, 840),
        "address": {"street": f"{rng.randrange(1, 200)} Marine Lines", "city": area.split()[0],
                    "state": "Maharashtra", "pincode": str(rng.randrange(400001, 700100)),
                    "country": "India"},
        "connection_ids": link_ids, "connection_details": links,
        "device_serials": serials, "device_details": devices,
        "payment_handles": [f"{handle}@okbank", f"{mobile}@okbank"],
        "contracts": contracts, "addon_services": addons,
        "static_ip_blocks": ([{"block": f"203.0.113.{rng.randrange(0, 250)}/30", "status": "ASSIGNED"}]
                             if segment in ("BUSINESS", "ENTERPRISE") else []),
        "premises_equipment": [{"item": "Wall-mounted ONT enclosure", "installed_on": opened.isoformat()}],
        "protection_plans": ([{"plan_id": f"PP{n:04d}", "type": "DEVICE_PROTECTION",
                               "cover_amount": 15000.0, "premium_annual": 899.0, "status": "ACTIVE"}]
                             if rng.random() < 0.3 else []),
        "roaming_profile": roaming,
        "account_manager": (f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
                            if segment in ("ENTERPRISE", "PRIORITY") else None),
        "self_care_portal": rng.choice(["ACTIVE", "ACTIVE", "LOCKED", "NOT_REGISTERED"]),
        "mobile_app_access": rng.choice(["ACTIVE", "ACTIVE", "NOT_REGISTERED"]),
        "sms_alerts": True, "email_alerts": True,
        "alternate_contact": {"name": f"{rng.choice(_FIRST)} {last}", "relationship": "FAMILY",
                              "mobile": f"55{20000000 + n * 97}"},
        "product_types": sorted({l["type"] for l in links} | {d["type"] for d in devices}),
    }


def subscriber_records() -> list[dict]:
    """The fixture set. Same seed, same records, every run."""
    rng = random.Random(SEED)
    return [_build(rng, n) for n in range(COUNT)]


def seed_subscribers(db: Session) -> int:
    count = 0
    for rec in subscriber_records():
        existing = (db.query(Subscriber)
                      .filter(Subscriber.customer_ref == rec["customer_ref"]).first())
        if existing:
            for key, value in rec.items():
                setattr(existing, key, value)
        else:
            db.add(Subscriber(**rec))
        count += 1
    db.commit()
    return count
