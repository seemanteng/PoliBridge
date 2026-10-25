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


def _probes(specs: list[str]) -> set[tuple]:
    """Every version a spec mentions, plus its neighbours, so two specs that disagree anywhere disagree on a probe."""
    out = set()
    for s in specs:
        for v in re.findall(r"\d+(?:\.\d+)*", s):
            p = parse(v)
            out.add(p)
            for i in range(len(p)):  # neighbours at the precision the issuer used, not invented sub-releases
                out.add(p[:i] + (p[i] + 1,) + p[i + 1:])
                if p[i] > 0:
                    out.add(p[:i] + (p[i] - 1,) + p[i + 1:])
    return out


def same_ranges(a: list[str], b: list[str]) -> bool:
    """True when two sources' affected-version lists cover the same versions, however they are written."""
    return all(affected(".".join(map(str, p)), a) == affected(".".join(map(str, p)), b) for p in _probes(a + b))


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())
