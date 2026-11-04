# Intentionally wrong: checks that counts match and never go negative, but ignores bracket type
# and nesting order, so "([)]" is wrongly accepted.
def is_balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0
