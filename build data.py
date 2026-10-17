"""Downloads all Mangrove reviews and writes site/data.json (small aggregates)."""
import json
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

BASE = "https://api.mangrove.reviews/reviews"
PAGE = 3000  # API rejects JSON requests above 3500
TOP_N = 10


def fetch_all():
    reviews, offset = [], 0
    while True:
        url = f"{BASE}?limit={PAGE}&offset={offset}"
        with urllib.request.urlopen(url, timeout=120) as resp:
            batch = json.load(resp)["reviews"]
        reviews.extend(batch)
        if len(batch) < PAGE:
            return reviews
        offset += PAGE


def classify(sub):
    """Return (type, label) for a subject URI."""
    s = (sub or "").strip()
    low = s.lower()
    if low.startswith("geo:"):
        coords, _, query = s[4:].partition("?")
        name = parse_qs(query).get("q", [""])[0].strip()
        if not name:
            try:
                lat, lon = coords.split(",")[:2]
                name = f"{float(lat):.4f}, {float(lon):.4f}"
            except ValueError:
                name = coords
        return "poi", name
    if low.startswith(("http://", "https://")):
        host = (urlparse(s).hostname or s).lower()
        return "website", re.sub(r"^www\.", "", host)
    m = re.match(r"(?:urn:)?isbn:(.+)", low)
    if m:
        return "book", m.group(1).replace("-", "")
    m = re.match(r"(?:urn:)?lei:(.+)", low)
    if m:
        return "company", m.group(1).upper()
    if low.startswith("urn:maresi:"):
        return "review", s
    return "other", s


def build(reviews):
    timestamps = []
    types = Counter()
    clients = Counter()
    reviewers = Counter()
    experience = Counter()
    flags = Counter()
    client_months = defaultdict(Counter)

    for r in reviews:
        p = r["payload"]
        timestamps.append(p["iat"])
        t, _ = classify(p.get("sub"))
        types[t] += 1
        meta = p.get("metadata") or {}
        client = meta.get("client_id")
        if client:
            clients[client] += 1
        month = datetime.fromtimestamp(p["iat"], tz=timezone.utc).strftime("%Y-%m")
        client_months[client or "unknown"][month] += 1
        reviewers[r.get("kid")] += 1
        if meta.get("experience_context"):
            experience[meta["experience_context"]] += 1
        if meta.get("is_affiliated"):
            flags["affiliated"] += 1
        if meta.get("is_generated"):
            flags["generated"] += 1
        if not meta.get("is_affiliated") and not meta.get("is_generated"):
            flags["neither"] += 1

    # Reviewer activity: how many reviewers wrote N reviews (counts only)
    buckets = {"1 review": 0, "2 to 5": 0, "6 to 20": 0, "21+": 0}
    for n in reviewers.values():
        key = "1 review" if n == 1 else "2 to 5" if n <= 5 else "6 to 20" if n <= 20 else "21+"
        buckets[key] += 1

    # Cumulative reviews per month, stacked by source (top 6 + other)
    all_months = sorted({m for c in client_months.values() for m in c})
    months = []
    if all_months:
        y, m = map(int, all_months[0].split("-"))
        ey, em = map(int, datetime.now(timezone.utc).strftime("%Y-%m").split("-"))
        while (y, m) <= (ey, em):
            months.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    ranked = sorted(client_months, key=lambda c: -sum(client_months[c].values()))
    groups = {c: [c] for c in ranked[:6]}
    if len(ranked) > 6:
        groups["other"] = ranked[6:]
    growth = {}
    for name, members in groups.items():
        run, out = 0, []
        for mo in months:
            run += sum(client_months[c][mo] for c in members)
            out.append(run)
        growth[re.sub(r"^https?://", "", name)] = out

    return {
        "reviewers": {"total": len(reviewers), "buckets": buckets},
        "experience": dict(experience),
        "flags": dict(flags),
        "growth": {"months": months, "series": growth},
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(reviews),
        "timestamps": sorted(timestamps),
        "types": dict(types),
        "clients": [{"label": k, "count": v} for k, v in clients.most_common(TOP_N)],
    }


if __name__ == "__main__":
    data = build(fetch_all())
    with open("site/data.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print("Wrote site/data.json with", data["total"], "reviews")
