import re

_PATTERN = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_duration(spec: str) -> int:
    m = _PATTERN.fullmatch(spec)
    if not spec or m is None:
        raise ValueError(f"invalid duration: {spec!r}")
    h, mnt, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mnt * 60 + s
