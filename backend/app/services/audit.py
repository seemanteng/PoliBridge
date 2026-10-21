from ..clock import clock
from ..models import AuditEvent


def log(db, actor, entity, entity_id, action, **detail):
    """Every state change goes through here (DO1 traceability)."""
    ev = AuditEvent(ts=clock.now(), actor=actor or "system", entity=entity, entity_id=entity_id,
                    action=action, detail=detail)
    db.add(ev)
    return ev
