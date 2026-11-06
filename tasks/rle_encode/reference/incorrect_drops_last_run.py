# Intentionally wrong: forgets to flush the final run.
def rle_encode(s: str) -> str:
    out = []
    count = 1
    for i in range(1, len(s)):
        if s[i] == s[i - 1]:
            count += 1
        else:
            out.append(s[i - 1] + (str(count) if count > 1 else ""))
            count = 1
    return "".join(out)
