"""Advisory feeds. PoliBridge polls each approved feed on the sweep timer, so nobody has to notice a warning and
type it in. The connectors are mocks: each feed's advisories are FeedItem rows that become readable once their
available_at passes, and a feed can be switched into a simulated outage.

Ingest rule: an advisory is ingested when its product is on the watchlist (some system's component record lists
it), or when it is severe (exploited, CVSS at or above feed_ingest_min_cvss, or unscored). Severe advisories are
always ingested because a system with no component record could be running anything. The rest are logged."""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..clock import clock
from ..config import TIMINGS
from ..models import Source, FeedItem, Asset, Advisory, Finding, Obligation, Task, Person
from ..rules import versions
from . import audit, intake
from .assignment import OPEN_STATES
from .common import raise_finding, notify, first_with_role


def watchlist(db) -> dict:
    stale = timedelta(days=TIMINGS["component_data_stale_days"])
    now = clock.now()
    products: dict[str, dict] = {}
    blind = []
    for a in db.scalars(select(Asset).order_by(Asset.name)):
        if a.inventory_verified_at is None or not a.components or now - a.inventory_verified_at > stale:
            blind.append({"asset": a.name, "why": "no component record" if not a.components or a.inventory_verified_at is None
                          else "component record is stale"})
        for c in a.components:
            p = products.setdefault(versions.norm(c.name), {"product": c.name, "systems": []})
            p["systems"].append(f"{a.name} ({c.version})")
    return {"products": sorted(products.values(), key=lambda p: p["product"].lower()), "blind_assets": blind}


def should_ingest(db, item: FeedItem, wl: dict | None = None) -> tuple[bool, str]:
    wl = wl or watchlist(db)
    hit = next((p for p in wl["products"] if versions.norm(p["product"]) == versions.norm(item.product)), None)
    if hit:
        return True, f"on the watchlist: {', '.join(hit['systems'])}"
    if item.kev:
        return True, "listed as exploited in the wild"
    if item.cvss is None:
        return True, "severity unknown, treated as critical"
    if item.cvss >= TIMINGS["feed_ingest_min_cvss"]:
        n = len(wl["blind_assets"])
        return True, (f"CVSS {item.cvss:g}: severe advisories are always matched"
                      + (f"; {n} system{'s' if n != 1 else ''} with no current component record could run it" if n else ""))
    return False, (f"not in any component inventory and CVSS {item.cvss:g} is below "
                   f"{TIMINGS['feed_ingest_min_cvss']:g}; logged, not ingested")


def _process(db, src: Source, item: FeedItem, wl: dict) -> None:
    now = clock.now()
    item.status, item.received_at = "received", now
    ok, why = (True, "canary self-test") if item.is_canary else should_ingest(db, item, wl)
    if not ok:
        item.outcome, item.reason = "skipped", why
        audit.log(db, "feed", "feed_item", item.id, "skipped", cve=item.cve, source=src.name, reason=why)
        return
    before = None if item.is_canary else db.scalar(select(Advisory).where(Advisory.cve == item.cve,
                                                                         Advisory.is_canary == False))  # noqa: E712
    had_conflict = bool(before and before.conflict)
    try:
        ob = intake.ingest_advisory(db, cve=item.cve, product=item.product, affected_versions=item.affected_versions,
                                    cvss=item.cvss, kev=item.kev, fixed_version=item.fixed_version,
                                    source_name=src.name, description=item.description, is_canary=item.is_canary,
                                    published_at=item.published_at, received_at=now)
    except ValueError as e:
        item.outcome, item.reason = "rejected", str(e)
        return
    item.advisory_id = ob.advisory_id
    adv = db.get(Advisory, ob.advisory_id)
    if before is None:
        item.outcome, item.reason = "ingested", why
    elif adv.conflict and not had_conflict:
        item.outcome, item.reason = "conflict", adv.conflict
    else:
        item.outcome, item.reason = "merged", f"already ingested from {before.sources[0]['source']}; added as corroboration"
    audit.log(db, "feed", "feed_item", item.id, item.outcome, cve=item.cve, source=src.name)


def poll(db, src: Source, only: FeedItem | None = None) -> dict:
    """One poll of one feed. Returns {ok, received, error}."""
    now = clock.now()
    src.last_polled_at = now
    if src.simulate_outage:
        src.consecutive_failures = (src.consecutive_failures or 0) + 1
        audit.log(db, "feed", "source", src.id, "poll_failed", failures=src.consecutive_failures)
        return {"source": src.name, "ok": False, "received": [], "error": "connection refused (simulated outage)"}
    q = select(FeedItem).where(FeedItem.source_id == src.id, FeedItem.status == "waiting", FeedItem.available_at <= now)
    items = [only] if only is not None else list(db.scalars(q.order_by(FeedItem.available_at, FeedItem.id)))
    wl = watchlist(db)
    for item in items:
        _process(db, src, item, wl)
    src.last_success_at, src.consecutive_failures = now, 0
    _resolve_silence(db, src)
    db.flush()
    return {"source": src.name, "ok": True, "received": [f"{i.cve} · {i.outcome}" for i in items], "error": None}


def poll_all(db) -> list[dict]:
    return [poll(db, s) for s in db.scalars(select(Source).where(Source.intake == "feed", Source.status == "approved"))]


def silence_subject(src: Source) -> str:
    return f"{src.name} feed is not delivering"


def _resolve_silence(db, src: Source):
    for f in db.scalars(select(Finding).where(Finding.kind == "feed_silent", Finding.status == "open",
                                              Finding.subject == silence_subject(src))):
        f.status = "resolved"
        audit.log(db, "feed", "finding", f.id, "resolved", because="feed polled successfully")


def health(db, src: Source) -> dict:
    now = clock.now()
    since = src.last_success_at or src.approved_at or src.created_at
    silent_h = (now - since).total_seconds() / 3600 if since else None
    failing = (src.consecutive_failures or 0) >= TIMINGS["feed_max_failed_polls"] or (
        silent_h is not None and silent_h > TIMINGS["feed_max_silence_hours"])
    state = "silent" if failing else "failing" if src.consecutive_failures else "healthy"
    waiting = db.scalar(select(FeedItem.id).where(FeedItem.source_id == src.id, FeedItem.status == "waiting",
                                                  FeedItem.is_canary == False).limit(1))  # noqa: E712
    return {"state": state, "last_polled_at": src.last_polled_at.isoformat() if src.last_polled_at else None,
            "last_success_at": src.last_success_at.isoformat() if src.last_success_at else None,
            "consecutive_failures": src.consecutive_failures or 0, "silent_hours": round(silent_h, 1) if silent_h else 0,
            "has_waiting": waiting is not None}


def check_health(db) -> list[str]:
    """A feed that stops delivering is a monitoring control that has failed without an alert, as the expired SSL
    certificate was at Equifax. Raise it to the source owner."""
    out = []
    for src in db.scalars(select(Source).where(Source.intake == "feed", Source.status == "approved")):
        h = health(db, src)
        if h["state"] != "silent":
            continue
        owner = db.get(Person, src.owner_id) if src.owner_id else first_with_role(db, "compliance")
        existing = db.scalar(select(Finding).where(Finding.kind == "feed_silent", Finding.status == "open",
                                                   Finding.subject == silence_subject(src)))
        if existing is None:
            raise_finding(db, "feed_silent", "High", silence_subject(src),
                          f"{h['consecutive_failures']} failed polls in a row; last successful poll "
                          f"{src.last_success_at:%d %b %H:%M}." if src.last_success_at else "No successful poll yet.",
                          assigned_to=owner)
            notify(db, person=owner, kind="feed_silent",
                   message=f"{src.name} has stopped delivering advisories. New warnings may be missed.")
            out.append(src.name)
    return out


def release_next(db) -> dict:
    """Demo control: make the next mock advisory available now and poll its feed."""
    item = db.scalar(select(FeedItem).where(FeedItem.status == "waiting", FeedItem.is_canary == False)  # noqa: E712
                     .order_by(FeedItem.available_at, FeedItem.id))
    if item is None:
        raise ValueError("every mock advisory has already been delivered")
    item.available_at = clock.now()
    src = db.get(Source, item.source_id)
    if src.status != "approved":
        raise ValueError(f"{src.name} is {src.status}; approve it before its feed is polled")
    return poll(db, src)


def item_view(db, i: FeedItem) -> dict:
    src = db.get(Source, i.source_id)
    adv = db.get(Advisory, i.advisory_id) if i.advisory_id else None
    ob = db.scalar(select(Obligation).where(Obligation.advisory_id == adv.id)) if adv else None
    first_ack = None
    if ob:
        acks = [t.acknowledged_at for t in db.scalars(select(Task).where(Task.obligation_id == ob.id,
                                                                         Task.acknowledged_at.is_not(None)))]
        first_ack = min(acks) if acks else None
    hours = lambda a, b: round((b - a).total_seconds() / 3600, 1) if a and b else None  # noqa: E731
    return {"id": i.id, "source": src.name, "cve": i.cve, "product": i.product, "cvss": i.cvss, "kev": i.kev,
            "affected_versions": i.affected_versions, "published_at": i.published_at.isoformat(),
            "available_at": i.available_at.isoformat(), "status": i.status,
            "received_at": i.received_at.isoformat() if i.received_at else None, "outcome": i.outcome,
            "reason": i.reason, "obligation_id": ob.id if ob else None, "is_canary": i.is_canary,
            "ingested_at": adv.ingested_at.isoformat() if adv and adv.ingested_at else None,
            "first_ack_at": first_ack.isoformat() if first_ack else None,
            "latency": {"publish_to_receive_h": hours(i.published_at, i.received_at),
                        "receive_to_ack_h": hours(i.received_at, first_ack),
                        "publish_to_ack_h": hours(i.published_at, first_ack)}}


def feeds_view(db) -> dict:
    from . import sources as S
    feeds = [S.source_view(db, s) for s in db.scalars(select(Source).where(Source.intake == "feed").order_by(Source.id))]
    items = [item_view(db, i) for i in db.scalars(select(FeedItem).where(FeedItem.is_canary == False)  # noqa: E712
                                                  .order_by(FeedItem.available_at.desc(), FeedItem.id.desc()))]
    return {"now": clock.now().isoformat(), "feeds": feeds, "watchlist": watchlist(db),
            "received": [i for i in items if i["status"] == "received"],
            "waiting": sorted([i for i in items if i["status"] == "waiting"], key=lambda i: i["available_at"]),
            "rule": {"min_cvss": TIMINGS["feed_ingest_min_cvss"], "max_silence_h": TIMINGS["feed_max_silence_hours"],
                     "max_failed_polls": TIMINGS["feed_max_failed_polls"]}}


def _advisories_from(db, src: Source) -> list[tuple[Advisory, datetime]]:
    """Every advisory this source reported, with when it reached PoliBridge from this source, newest first."""
    out = []
    for adv in db.scalars(select(Advisory).where(Advisory.is_canary == False)):  # noqa: E712
        entry = next((e for e in adv.sources or [] if e["source"] == src.name), None)
        if entry:
            out.append((adv, datetime.fromisoformat(entry["received_at"])))
        elif not adv.sources and adv.source_id == src.id:  # recorded before sources were tracked per advisory
            out.append((adv, adv.received_at or adv.published_at))
    return sorted(out, key=lambda x: x[1], reverse=True)


NEW_HOURS = 24


def summary(db, src: Source) -> dict:
    """One line for the Sources list: how many alerts, how many new, how many systems they touch."""
    now = clock.now()
    advs = _advisories_from(db, src)
    systems, waiting = set(), 0
    for adv, _ in advs:
        ob = db.scalar(select(Obligation).where(Obligation.advisory_id == adv.id))
        for t in db.scalars(select(Task).where(Task.obligation_id == ob.id, Task.status.in_(OPEN_STATES))):
            systems.add(t.asset_id)
            waiting += t.acknowledged_at is None
    return {"alerts": len(advs), "new": sum(now - at < timedelta(hours=NEW_HOURS) for _, at in advs),
            "systems_affected": len(systems), "awaiting_ack": waiting,
            "queued": db.scalar(select(func.count(FeedItem.id)).where(FeedItem.source_id == src.id,
                                                                      FeedItem.status == "waiting",
                                                                      FeedItem.is_canary == False)) or 0}  # noqa: E712


def dashboard(db, src: Source) -> dict:
    """For one feed: every system with its software, each alert the feed delivered, and where each system stands."""
    from . import sources as S, views
    now = clock.now()
    stale = timedelta(days=TIMINGS["component_data_stale_days"])
    systems = []
    for a in db.scalars(select(Asset).order_by(Asset.name)):
        owner = db.get(Person, a.owner_id) if a.owner_id else None
        inv = ("none" if a.inventory_verified_at is None or not a.components else
               "stale" if now - a.inventory_verified_at > stale else "current")
        systems.append({"id": a.id, "name": a.name, "owner": owner.name if owner and owner.active else None,
                        "tags": [t for t, on in (("Internet-facing", a.internet_facing), ("Critical", a.critical),
                                                 ("Personal data", a.data_sensitivity in ("confidential", "restricted")))
                                 if on],
                        "software": [f"{c.name} {c.version}" for c in a.components], "inventory": inv})
    alerts = []
    for adv, at in _advisories_from(db, src):
        ob = db.scalar(select(Obligation).where(Obligation.advisory_id == adv.id))
        est = views.estate(db, ob)
        tasks = {t.asset_id: t for t in db.scalars(select(Task).where(Task.obligation_id == ob.id).order_by(Task.id))}
        cells = {}
        for r in est["rows"]:
            t = tasks.get(r["asset_id"])
            cells[r["asset_id"]] = {"state": r["state"], "task_id": r["task_id"], "task_ref": r["task_ref"],
                                    "task_status": r["task_status"], "impact": t.impact if t else None,
                                    "acknowledged": bool(t and t.acknowledged_at), "open": bool(t and t.status in OPEN_STATES)}
        alerts.append({"obligation_id": ob.id, "cve": adv.cve, "product": adv.product, "cvss": adv.cvss, "kev": adv.kev,
                       "impact": ob.impact, "received_at": at.isoformat(), "new": now - at < timedelta(hours=NEW_HOURS),
                       "also_from": [e["source"] for e in adv.sources or [] if e["source"] != src.name],
                       "awaiting_ack": sum(c["open"] and not c["acknowledged"] for c in cells.values()),
                       "counts": est["counts"], "cells": cells})
    items = list(db.scalars(select(FeedItem).where(FeedItem.source_id == src.id, FeedItem.is_canary == False)  # noqa: E712
                            .order_by(FeedItem.available_at.desc(), FeedItem.id.desc())))
    return {"now": now.isoformat(), "source": S.source_view(db, src), "systems": systems, "alerts": alerts,
            "log": [item_view(db, i) for i in items if i.status == "received"],
            "upcoming": [item_view(db, i) for i in reversed(items) if i.status == "waiting"],
            "rule": {"min_cvss": TIMINGS["feed_ingest_min_cvss"], "max_silence_h": TIMINGS["feed_max_silence_hours"],
                     "max_failed_polls": TIMINGS["feed_max_failed_polls"], "new_hours": NEW_HOURS}}


def release_next_from(db, src: Source) -> dict:
    """Demo control for one feed: make its next queued mock advisory available now and poll it."""
    item = db.scalar(select(FeedItem).where(FeedItem.source_id == src.id, FeedItem.status == "waiting",
                                            FeedItem.is_canary == False).order_by(FeedItem.available_at, FeedItem.id))  # noqa: E712
    if item is None:
        raise ValueError(f"{src.name} has no more queued mock alerts")
    item.available_at = clock.now()
    return poll(db, src)
