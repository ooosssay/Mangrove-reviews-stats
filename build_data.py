"""Downloads all Mangrove reviews and writes site/data.json (anonymous rows, no subject names)."""
import json
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

BASE = "https://api.mangrove.reviews/reviews"
PAGE = 3000  # API rejects JSON requests above 3500
# Tells the Mangrove team who is calling. Replace with your own repo URL.
USER_AGENT = "mangrove-reviews-stats (https://github.com/ooosssay/Mangrove-reviews-stats)"
TYPES = ["poi", "website", "company", "book", "other"]


def fetch_all():
    reviews, offset = [], 0
    while True:
        url = f"{BASE}?limit={PAGE}&offset={offset}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            batch = json.load(resp)["reviews"]
        reviews.extend(batch)
        if len(batch) < PAGE:
            return reviews
        offset += PAGE


def classify(sub):
    """Return the subject type for a subject URI (names are never published)."""
    low = (sub or "").strip().lower()
    if low.startswith("geo:"):
        return "poi"
    if low.startswith(("http://", "https://")):
        return "website"
    if re.match(r"(?:urn:)?isbn:", low):
        return "book"
    if re.match(r"(?:urn:)?lei:", low):
        return "company"
    return "other"  # includes reviews of reviews (urn:maresi:)


def build(reviews):
    clients, experiences, reviewers = {}, {}, {}

    def idx(table, key):
        return table.setdefault(key, len(table))

    rows = []
    for r in sorted(reviews, key=lambda r: r["payload"]["iat"]):
        p = r["payload"]
        meta = p.get("metadata") or {}
        client = re.sub(r"^https?://", "", meta.get("client_id") or "unknown")
        exp = meta.get("experience_context")
        flags = (1 if meta.get("is_affiliated") else 0) | (2 if meta.get("is_generated") else 0)
        rows.append([
            p["iat"],
            TYPES.index(classify(p.get("sub"))),
            idx(clients, client),
            idx(reviewers, r.get("kid")),  # anonymous number, key is never published
            idx(experiences, exp) if exp else -1,
            flags,
        ])

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "types": TYPES,
        "clients": list(clients),
        "experiences": list(experiences),
        "rows": rows,  # [timestamp, type, client, reviewer, experience, flags]
    }


if __name__ == "__main__":
    data = build(fetch_all())
    with open("site/data.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print("Wrote site/data.json with", len(data["rows"]), "reviews")
