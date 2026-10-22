import re


def parse(v: str) -> tuple:
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts)


def _cmp(a: tuple, b: tuple) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def in_range(version: str, spec: str) -> bool:
    """spec is 'x', 'lo-hi' (inclusive), '<=hi' / '<hi', '>=lo' / '>lo', or several of the
    comparison forms separated by spaces (all must hold), e.g. '>=2.0 <2.3.5'."""
    v = parse(version)
    spec = spec.strip()
    if " " in spec:
        return all(in_range(version, part) for part in spec.split())
    if spec.startswith(">="):
        return _cmp(v, parse(spec[2:])) >= 0
    if spec.startswith(">"):
        return _cmp(v, parse(spec[1:])) > 0
    if spec.startswith("<="):
        return _cmp(v, parse(spec[2:])) <= 0
    if spec.startswith("<"):
        return _cmp(v, parse(spec[1:])) < 0
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return _cmp(v, parse(lo)) >= 0 and _cmp(v, parse(hi)) <= 0
    return _cmp(v, parse(spec)) == 0


def affected(version: str, specs: list[str]) -> bool:
    return any(in_range(version, s) for s in specs)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())
