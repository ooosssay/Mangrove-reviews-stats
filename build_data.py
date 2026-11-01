"""Loads Mangrove reviews from the daily dump (API as fallback) and writes site/data.json (anonymous rows, no subject names)."""
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

BASE = "https://api.mangrove.reviews/reviews"
DUMP_URL = "https://api.mangrove.reviews/dump"
PAGE = 1500
USER_AGENT = "mangrove-reviews-stats (https://github.com/ooosssay/Mangrove-reviews-stats)"
TYPES = ["poi", "website", "company", "book", "other"]
PAUSE = 2  # seconds between requests
EXAMPLE_SUB = re.compile(r"^(https://example\.com/?|geo:0,0(\?.*)?)$", re.I)
NOT_REVIEWS = {"delete", "report_abuse", "equivalence", "identity"}


def get_json(url, tries=5):
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
    seen = {}
    page, offset, n = PAGE, 0, 0
    while True:
        try:
            batch = get_json(f"{BASE}?limit={page}&offset={offset}")["reviews"]
        except urllib.error.HTTPError as err:
            if err.code == 413 and page > 100:
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


def fetch_dump():
    """Download the latest daily dump (the server redirects to the file)."""
    req = urllib.request.Request(DUMP_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)["reviews"]


def action_of(record):
    return record["payload"].get("action") or record.get("action")


def maresi_target(sub):
    sub = sub or ""
    return sub[len("urn:maresi:"):] if sub.lower().startswith("urn:maresi:") else None


def filter_dump(records):
    """Keep what /reviews returns by default: no claims, deletions or abuse reports,
    only the latest version of an edited review, no deleted reviews, no example subjects."""
    print("Dump records by type:", dict(Counter(action_of(r) or "review" for r in records)), flush=True)
    signatures = {r["signature"] for r in records}
    superseded, deleted, edits_by_target = set(), set(), {}
    unlinked_edits = 0
    for r in records:
        target = maresi_target(r["payload"].get("sub"))
        if action_of(r) == "edit":
            if target in signatures:
                superseded.add(target)
                edits_by_target.setdefault(target, []).append(r)
            else:
                unlinked_edits += 1
        elif target and action_of(r) == "delete":
            deleted.add(target)
    stale_edits = set()
    for group in edits_by_target.values():
        newest = max(group, key=lambda r: r["payload"]["iat"])
        stale_edits.update(r["signature"] for r in group if r is not newest)

    kept, seen, dropped = [], set(), Counter()
    for r in records:
        sig = r["signature"]
        if action_of(r) in NOT_REVIEWS:
            dropped["claims, deletions, abuse reports"] += 1
        elif sig in superseded:
            dropped["superseded by an edit"] += 1
        elif sig in stale_edits:
            dropped["older edit of the same review"] += 1
        elif sig in deleted:
            dropped["deleted"] += 1
        elif sig in seen:
            dropped["duplicate"] += 1
        elif EXAMPLE_SUB.match(r["payload"].get("sub") or ""):
            dropped["example subject"] += 1
        else:
            seen.add(sig)
            kept.append(r)
    n_edits = sum(1 for r in records if action_of(r) == "edit")
    print(f"Edits: {n_edits} total, {n_edits - unlinked_edits} point at a record in the dump, "
          f"{unlinked_edits} do not", flush=True)
    sample = next((r for r in records if action_of(r) == "edit"), None)
    if sample:
        print(f"Sample edit subject: {str(sample['payload'].get('sub'))[:30]}", flush=True)
    print("Dropped:", dict(dropped), flush=True)
    return kept


def load_reviews():
    try:
        records = fetch_dump()
        reviews = filter_dump(records)
        if not reviews:
            raise ValueError("dump contained no reviews")
        newest = max(r["payload"]["iat"] for r in reviews)
        age = (datetime.now(timezone.utc).timestamp() - newest) / 3600
        print(f"Dump: {len(records)} records, {len(reviews)} reviews kept; "
              f"newest review is {age:.1f} hours old", flush=True)
        return reviews
    except Exception as err:
        print(f"Dump unavailable ({err}); falling back to the API", flush=True)
        return fetch_all()


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
    return "other"


ALIASES = {"mapcomplete.osm.be": "mapcomplete.org", "dev.mapcomplete.org": "mapcomplete.org"}

CODE_HOSTS = {"github.com", "codeberg.org", "gitlab.com", "bitbucket.org"}
PAGES_SUFFIXES = (".github.io", ".codeberg.page", ".gitlab.io")


def client_name(client_id):
    """Reduce a client_id to its site.

    https://mapcomplete.org/food.html?z=1  -> mapcomplete.org
    https://github.com/user/repo/blob/x    -> github.com/user/repo
    https://user.github.io/project/a.html  -> user.github.io/project
    urn:example:app-one                    -> urn:example:app-one (kept whole)
    """
    s = (client_id or "").strip()
    if not s:
        return "unknown"
    if s.lower().startswith("urn:"):
        return s  # URNs have no host, so keep the whole identifier
    u = urlparse(s if "://" in s else "//" + s)
    if not u.hostname:
        return s
    host = re.sub(r"^www\.", "", u.hostname.lower())
    host = ALIASES.get(host, host)
    parts = [p for p in u.path.split("/") if p]
    if host in CODE_HOSTS:
        return "/".join([host] + parts[:2])
    if host.endswith(PAGES_SUFFIXES) and parts and "." not in parts[0]:
        return f"{host}/{parts[0]}"
    return host


OTHER_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]{0,19}):(?:([a-z0-9][a-z0-9-]{0,19}):)?")


def other_kind(sub):
    """Describe what KIND of subject an 'other' review is about (never the subject itself)."""
    low = (sub or "").strip().lower()
    if low.startswith("urn:maresi:"):
        return "Reviews of reviews"
    m = OTHER_SCHEME.match(low)
    if not m:
        return "Unrecognized"
    if m.group(1) == "urn" and m.group(2):
        return f"urn:{m.group(2)}"
    return m.group(1) + ":"


def build(reviews):
    clients, experiences, reviewers = {}, {}, {}
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
    other_counts = Counter()
    for r in sorted(reviews, key=lambda r: r["payload"]["iat"]):
        p = r["payload"]
        meta = p.get("metadata") or {}
        client = client_name(meta.get("client_id"))
        exp = meta.get("experience_context")
        # bits: 1 affiliated, 2 bot, 4 personal experience
        flags = (
            (1 if meta.get("is_affiliated") is True else 0)
            | (2 if meta.get("is_generated") is True else 0)
            | (4 if meta.get("is_personal_experience") is True else 0)
        )
        sub = p.get("sub")
        if p.get("action") == "edit" and r.get("original_sub"):
            sub = r["original_sub"]
        kind = classify(sub)
        if kind == "other":
            other_counts[other_kind(sub)] += 1
        rows.append([
            p["iat"],
            TYPES.index(kind),
            client_idx(client),
            idx(reviewers, r.get("did") or r.get("kid")),  # keys of one identity count as one reviewer
            idx(experiences, exp) if exp else -1,
            flags,
        ])

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "types": TYPES,
        "clients": display,
        "experiences": list(experiences),
        "other_kinds": [{"label": k, "count": n} for k, n in other_counts.most_common(3)],
        "rows": rows,  # [timestamp, type, client, reviewer, experience, flags]
    }


if __name__ == "__main__":
    reviews = load_reviews()
    data = build(reviews)

    tally = Counter(row[2] for row in data["rows"])
    imported = {}
    for r in reviews:
        meta = r["payload"].get("metadata") or {}
        if meta.get("data_source"):
            name = client_name(meta.get("client_id")).lower()
            imported.setdefault(name, Counter())[str(meta["data_source"])[:60]] += 1
    print("Top sources:")
    for i, n in tally.most_common(25):
        name = data["clients"][i]
        line = f"  {n:6d}  {name}"
        ds = imported.get(name.lower())
        if ds:
            line += "   [data_source: " + ", ".join(f"{k} x{v}" for k, v in ds.most_common(3)) + "]"
        print(line)

    with open("site/data.json", "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print("Wrote site/data.json with", len(data["rows"]), "reviews")
