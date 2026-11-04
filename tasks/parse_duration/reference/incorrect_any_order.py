# Intentionally wrong: accepts units in any order and repeated units ("5m1h", "1h1h").
import re


def parse_duration(spec: str) -> int:
    parts = re.findall(r"(\d+)([hms])", spec)
    if not parts or "".join(n + u for n, u in parts) != spec:
        raise ValueError(f"invalid duration: {spec!r}")
    scale = {"h": 3600, "m": 60, "s": 1}
    return sum(int(n) * scale[u] for n, u in parts)
