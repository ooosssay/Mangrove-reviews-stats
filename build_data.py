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
# A source only becomes a clickable link if it appears this many times overall
# and looks like a normal site address. Sources are self-declared by review
# writers, so this keeps one-off or junk entries from becoming links.
MIN_REVIEWS_FOR_LINK = 5
LINKABLE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}(/[A-Za-z0-9._~-]+)*$")
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


# Hosts that serve many unrelated projects: keep the owner/repo part of the path
CODE_HOSTS = {"github.com", "codeberg.org", "gitlab.com", "bitbucket.org"}
# Per-user page hosts (user.github.io/project): keep the first path part (the project)
PAGES_SUFFIXES = (".github.io", ".codeberg.page", ".gitlab.io")


def client_name(client_id):
    """Reduce a client_id to its site.

    https://mapcomplete.org/food.html?z=1  -> mapcomplete.org
    https://github.com/user/repo/blob/x    -> github.com/user/repo
    https://user.github.io/project/a.html  -> user.github.io/project
    """
    s = (client_id or "").strip()
    if not s:
        return "unknown"
    u = urlparse(s if "://" in s else "//" + s)
    if not u.hostname:
        return s
    host = re.sub(r"^www\.", "", u.hostname.lower())
    parts = [p for p in u.path.split("/") if p]
    if host in CODE_HOSTS:
        return "/".join([host] + parts[:2])
    if host.endswith(PAGES_SUFFIXES) and parts and "." not in parts[0]:
        return f"{host}/{parts[0]}"
    return host


def build(reviews):
    clients, experiences, reviewers = {}, {}, {}  # lowercase key -> index
    display, per_client = [], {}

    def idx(table, key):
        return table.setdefault(key, len(table))

    def client_idx(name):
        key = name.lower()
        if key not in clients:
            clients[key] = len(display)
            display.append(name)
        i = clients[key]
        per_client[i] = per_client.get(i, 0) + 1
        return i

    rows = []
    for r in sorted(reviews, key=lambda r: r["payload"]["iat"]):
        p = r["payload"]
        meta = p.get("metadata") or {}
        client = client_name(meta.get("client_id"))
        exp = meta.get("experience_context")
        flags = (1 if meta.get("is_affiliated") else 0) | (2 if meta.get("is_generated") else 0)
        rows.append([
            p["iat"],
            TYPES.index(classify(p.get("sub"))),
            client_idx(client),
            idx(reviewers, r.get("kid")),  # anonymous number, key is never published
            idx(experiences, exp) if exp else -1,
            flags,
        ])

    linkable = [
        i for i, name in enumerate(display)
        if per_client[i] >= MIN_REVIEWS_FOR_LINK and LINKABLE.match(name)
    ]

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "types": TYPES,
        "clients": display,
        "linkable": linkable,
        "experiences": list(experiences),
        "rows": rows,  # [timestamp, type, client, reviewer, experience, flags]
    }


if __name__ == "__main__":
    data = build(fetch_all())
    with open("site/data.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print("Wrote site/data.json with", len(data["rows"]), "reviews")
