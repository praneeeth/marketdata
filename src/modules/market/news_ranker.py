from __future__ import annotations

import re
from collections import Counter
from datetime import datetime


POSITIVE_HINTS = (
    "order win",
    "bags order",
    "growth",
    "upgrade",
    "record high",
    "positive",
    "stake increase",
    "buyback",
    "turnaround",
    "beats estimates",
)

NEGATIVE_HINTS = (
    "downgrade",
    "stake sale",
    "loss",
    "plunge",
    "lawsuit",
    "risk",
    "violation",
    "penalty",
    "negative",
    "delisting",
)

# Headline words that signal a material corporate event.
_EVENT_WORDS = (
    "results", "earnings", "stake", "buyback", "dividend", "bonus", "split",
    "acquisition", "merger", "trading halt", "suspension", "lock-in",
)

# Common headline words that are never a topic.
_STOPWORDS = frozenset(
    """the and for with from that this into over after amid says said will its are was were has have
    ltd limited company announcement today news shares share stock stocks india indian report reports
    update updates year quarter crore""".split()
)


def _to_naive_local(dt: datetime) -> datetime:
    """Convert to a naive datetime in local time, for comparison with datetime.now()."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


def parse_news_time(value: str | datetime | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_naive_local(value)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("T", " ").replace("Z", "+00:00")
    full_fmts = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    )
    for fmt in full_fmts:
        try:
            return _to_naive_local(datetime.strptime(normalized, fmt))
        except Exception:
            continue

    # Common month-day formats (no year) get the current year.
    for fmt in ("%m-%d %H:%M:%S", "%m-%d %H:%M", "%m/%d %H:%M:%S", "%m/%d %H:%M"):
        try:
            partial = datetime.strptime(normalized, fmt)
            now = datetime.now()
            return partial.replace(year=now.year)
        except Exception:
            continue

    try:
        return _to_naive_local(datetime.fromisoformat(normalized))
    except Exception:
        return None


def dedupe_news_items(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for it in items:
        source = str(it.get("source") or "")
        external_id = str(it.get("external_id") or "")
        title = str(it.get("title") or "")
        key = (source, external_id, title)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _sentiment_from_text(text: str) -> str:
    lowered = text.lower()
    pos = sum(1 for k in POSITIVE_HINTS if k in lowered)
    neg = sum(1 for k in NEGATIVE_HINTS if k in lowered)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def rank_news_items(items: list[dict], symbol: str = "") -> list[dict]:
    def score(it: dict) -> tuple[float, float]:
        title = str(it.get("title") or "")
        content = str(it.get("content") or "")
        text = f"{title} {content}"
        importance = float(it.get("importance") or 0)
        s = importance * 5.0

        if symbol and symbol in str(it.get("symbols") or []):
            s += 2.0
        lowered = title.lower()
        if any(k in lowered for k in _EVENT_WORDS):
            s += 2.0
        if "announcement" in lowered:
            s += 1.0

        ts = parse_news_time(str(it.get("time") or "")) or datetime.min
        s2 = ts.timestamp() if ts != datetime.min else 0
        return s, s2

    return sorted(items, key=score, reverse=True)


def summarize_news_topics(items: list[dict], max_topics: int = 6) -> dict:
    if not items:
        return {
            "summary": "No notable news themes recently",
            "topics": [],
            "sentiment": "neutral",
            "counts": {"positive": 0, "negative": 0, "neutral": 0},
        }

    word_counter: Counter[str] = Counter()
    senti_counter: Counter[str] = Counter()

    for it in items:
        title = str(it.get("title") or "")
        content = str(it.get("content") or "")
        text = f"{title} {content}".strip()
        sentiment = _sentiment_from_text(text)
        senti_counter[sentiment] += 1

        words = re.findall(r"[A-Za-z][A-Za-z0-9&]{2,}", title)
        for w in words:
            if w.lower() in _STOPWORDS:
                continue
            word_counter[w] += 1

    topics = [w for w, _ in word_counter.most_common(max_topics)]
    if senti_counter["positive"] > senti_counter["negative"]:
        senti = "positive"
    elif senti_counter["negative"] > senti_counter["positive"]:
        senti = "negative"
    else:
        senti = "neutral"

    mood = {"positive": "leaning positive", "negative": "leaning negative"}.get(senti, "neutral")
    if topics:
        summary = f"Themes: {', '.join(topics[: max_topics])}; overall sentiment {mood}"
    else:
        summary = f"Little news available; overall sentiment {mood}"

    return {
        "summary": summary,
        "topics": topics,
        "sentiment": senti,
        "counts": {
            "positive": int(senti_counter["positive"]),
            "negative": int(senti_counter["negative"]),
            "neutral": int(senti_counter["neutral"]),
        },
    }
