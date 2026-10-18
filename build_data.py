"""Downloads all Mangrove reviews and writes site/data.json (anonymous rows, no subject names)."""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

BASE = "https://api.mangrove.reviews/reviews"
PAGE = 1500  # reviews per request; the server rejects very large responses (413)
# Tells the Mangrove team who is calling. Replace with your own repo URL.
USER_AGENT = "mangrove-reviews-stats (https://github.com/ooosssay/Mangrove-reviews-stats)"
TYPES = ["poi", "website", "company", "book", "other"]


PAUSE = 2  # seconds to wait between requests, to be gentle on the server


def get_json(url, tries=5):
    """GET a JSON URL, waiting and retrying when the server says to slow down."""
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                retry_after = err.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 10 * (attempt + 1)
                print(f"HTTP {err.code}, waiting {wait}s before retrying", flush=True)
                time.sleep(min(wait, 120))
                continue
            raise
        except urllib.error.URLError as err:
            if attempt < tries - 1:
                print(f"Network error ({err.reason}), retrying", flush=True)
                time.sleep(10 * (attempt + 1))
                continue
            raise


def fetch_all():
    """Fetch every review. A page with fewer reviews than asked for is NOT treated
    as the last page (the server may drop some rows after paging); we only stop
    on an empty page, or one that adds nothing new."""
    seen = {}  # signature -> review, so overlapping pages never double count
    page, offset, n = PAGE, 0, 0
    while True:
        try:
            batch = get_json(f"{BASE}?limit={page}&offset={offset}")["reviews"]
        except urllib.error.HTTPError as err:
            if err.code == 413 and page > 100:  # response too large: ask for less
                page //= 2
                print(f"HTTP 413, reducing page size to {page}", flush=True)
                continue
            raise
        n += 1
        new = 0
        for r in batch:
            if r["signature"] not in seen:
                seen[r["signature"]] = r
                new += 1
        print(f"Page {n} (offset {offset}, limit {page}): {len(batch)} returned, "
              f"{new} new, {len(seen)} total", flush=True)
        if not batch or new == 0:
            return list(seen.values())
        offset += len(batch)
        time.sleep(PAUSE)


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
    display = []

    def idx(table, key):
        return table.setdefault(key, len(table))

    def client_idx(name):
        key = name.lower()
        if key not in clients:
            clients[key] = len(display)
            display.append(name)
        return clients[key]

    rows = []
    for r in sorted(reviews, key=lambda r: r["payload"]["iat"]):
        p = r["payload"]
        meta = p.get("metadata") or {}
        client = client_name(meta.get("client_id"))
        exp = meta.get("experience_context")
        # Flag fields must equal true when present (Mangrove spec). Bits: 1 affiliated, 2 bot, 4 personal experience
        flags = (
            (1 if meta.get("is_affiliated") is True else 0)
            | (2 if meta.get("is_generated") is True else 0)
            | (4 if meta.get("is_personal_experience") is True else 0)
        )
        rows.append([
            p["iat"],
            TYPES.index(classify(p.get("sub"))),
            client_idx(client),
            idx(reviewers, r.get("kid")),  # anonymous number, key is never published
            idx(experiences, exp) if exp else -1,
            flags,
        ])

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "types": TYPES,
        "clients": display,
        "experiences": list(experiences),
        "rows": rows,  # [timestamp, type, client, reviewer, experience, flags]
    }


if __name__ == "__main__":
    data = build(fetch_all())
    with open("site/data.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print("Wrote site/data.json with", len(data["rows"]), "reviews")
