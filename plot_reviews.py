import json
import urllib.request
from collections import Counter
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = "https://api.mangrove.reviews/reviews"
PAGE = 3000  # API rejects JSON requests above 3500

reviews = []
offset = 0
while True:
    url = f"{BASE}?limit={PAGE}&offset={offset}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        batch = json.load(resp)["reviews"]
    reviews.extend(batch)
    if len(batch) < PAGE:
        break
    offset += PAGE

months = Counter()
for r in reviews:
    iat = r["payload"]["iat"]
    d = datetime.fromtimestamp(iat, tz=timezone.utc)
    months[d.strftime("%Y-%m")] += 1

keys = sorted(months)
counts = [months[k] for k in keys]

# Cumulative total
total, running = 0, []
for c in counts:
    total += c
    running.append(total)

fig, ax = plt.subplots(figsize=(11, 5))
ax.bar(keys, counts, color="#2a9d8f", label="New reviews per month")
ax.set_ylabel("Reviews per month")
ax.tick_params(axis="x", rotation=90)
step = max(1, len(keys) // 24)
for i, lbl in enumerate(ax.get_xticklabels()):
    lbl.set_visible(i % step == 0)

ax2 = ax.twinx()
ax2.plot(keys, running, color="#e76f51", label="Cumulative")
ax2.set_ylabel("Total reviews")

ax.set_title(f"Mangrove reviews over time ({total} total)")
fig.tight_layout()
fig.savefig("reviews_over_time.png", dpi=150)
print("Saved chart with", total, "reviews")
