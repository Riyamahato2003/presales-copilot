from __future__ import annotations
import re

_THOUSANDS = re.compile(r"(?<=\d)[ \u00a0\u202f,](?=\d{3}(?!\d))")
_STOP = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "at", "be", "is", "are", "with",
         "as", "by", "per", "may", "will", "shall", "must", "from", "up", "that", "this", "its"}


def normalize(s: str) -> str:
    s = s.casefold().replace("µ", "u").replace("\u00b5", "u")
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = _THOUSANDS.sub("", s)                 # "6 500" / "6,500" -> "6500"
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def numbers_in(s: str) -> set[str]:
    out = set()
    for m in re.findall(r"\d+(?:\.\d+)?", normalize(s)):
        out.add(m.rstrip("0").rstrip(".") if "." in m else m)
    return out


def tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", normalize(s)) if t not in _STOP]
