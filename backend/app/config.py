from pathlib import Path
import yaml

_ROOT = Path(__file__).resolve().parent.parent


def load_timings() -> dict:
    with open(_ROOT / "timings.yaml") as f:
        return yaml.safe_load(f)


TIMINGS = load_timings()
DB_URL = f"sqlite:///{_ROOT / 'polibridge.db'}"
