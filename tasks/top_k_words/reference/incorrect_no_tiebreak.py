# Intentionally wrong: ties are left in first-seen order instead of alphabetical.
import re
from collections import Counter


def top_k_words(text: str, k: int) -> list[str]:
    counts = Counter(w.lower() for w in re.findall(r"[A-Za-z]+", text))
    return [w for w, _ in counts.most_common(k)]
