import re
from collections import Counter


def top_k_words(text: str, k: int) -> list[str]:
    counts = Counter(w.lower() for w in re.findall(r"[A-Za-z]+", text))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:k]]
