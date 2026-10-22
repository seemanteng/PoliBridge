"""Injectable clock. Expiry and escalation are time-driven, so demos and tests
need to move time without sleeping."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR

_STATE = DATA_DIR / "clock_state.json"


class Clock:
    def __init__(self):
        self._offset = timedelta(0)
        self._frozen: datetime | None = None

    def now(self) -> datetime:
        if self._frozen is not None:
            return self._frozen
        return datetime.now(timezone.utc).replace(tzinfo=None) + self._offset

    @property
    def simulated(self) -> bool:
        return self._frozen is not None

    def set(self, when: datetime):
        self._frozen = when

    def save(self):
        """Persist the simulated time so a server restart keeps the demo consistent."""
        _STATE.write_text(json.dumps({"frozen": self._frozen.isoformat() if self._frozen else None}))

    def load(self):
        try:
            v = json.loads(_STATE.read_text()).get("frozen")
            self._frozen = datetime.fromisoformat(v) if v else None
        except (FileNotFoundError, ValueError):
            pass

    def advance(self, **kw):
        self._frozen = self.now() + timedelta(**kw)

    def unfreeze(self):
        self._frozen = None
        self._offset = timedelta(0)


clock = Clock()


def add_working_days(start: datetime, days: int) -> datetime:
    cur, left = start, days
    while left > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            left -= 1
    return cur
