import os
from pathlib import Path
import yaml

_ROOT = Path(__file__).resolve().parent.parent
# Everything PoliBridge remembers (database, clock) lives here. Point POLIBRIDGE_DATA_DIR somewhere else to run a
# throwaway copy, such as a test or a rehearsal, without touching the real demo data.
DATA_DIR = Path(os.getenv("POLIBRIDGE_DATA_DIR") or _ROOT)
DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_timings() -> dict:
    with open(_ROOT / "timings.yaml") as f:
        return yaml.safe_load(f)


TIMINGS = load_timings()
DB_URL = f"sqlite:///{DATA_DIR / 'polibridge.db'}"
