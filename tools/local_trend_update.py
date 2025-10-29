#!/usr/bin/env python3
# local_trend_update.py — Detect trending topics from your YouTube DB
# ----------------------------------------------------------------------
# Automatically detects collection (videos/processed)
# Handles both string and Date types for snippet.publishedAt
# Output: trends/local_trending_weights.json
# ----------------------------------------------------------------------

import os, json, re, datetime as dt, argparse
from pymongo import MongoClient
from collections import Counter
import numpy as np
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

STOPWORDS = set("""
the a an is are was were i you he she it they we to for in of on and or
with from that this these those your my our their by as at about be have
has had not no yes new how what when where why who which do does did up
out over under into more most less least get make go see watch video
""".split())

def log(msg): 
    print(f"[{dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}", flush=True)

def clean_text(t: str) -> list[str]:
    t = re.sub(r"[^a-zA-Z0-9\s]", " ", str(t).lower())
    words = [w for w in t.split() if 3 <= len(w) <= 15 and w not in STOPWORDS]
    return words

def detect_collection(db) -> str:
    """Auto-pick collection with most recent videos."""
    candidates = ["videos", "processed"]
    best, latest = None, dt.datetime(2000, 1, 1)
    for c in candidates:
        coll = db[c]
        doc = coll.find_one(sort=[("snippet.publishedAt", -1)], projection={"snippet.publishedAt": 1})
        if doc and "snippet" in doc and "publishedAt" in doc["snippet"]:
            pub = doc["snippet"]["publishedAt"]
            if isinstance(pub, dict) and "$date" in pub:
                pub = dt.datetime.fromisoformat(pub["$date"].replace("Z", "+00:00"))
            elif isinstance(pub, str):
                pub = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if isinstance(pub, dt.datetime):
                pub = pub.replace(tzinfo=None)
                if pub > latest:
                    best, latest = c, pub
    return best or "videos"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=int(os.getenv("LOCAL_TREND_DAYS", "7")),
                    help="Lookback window in days (default=7)")
    ap.add_argument("--outdir", type=str, default="./trends")
    args = ap.parse_args()

    mongo_uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    db_name = os.getenv("MONGO_DB", "ytscan")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    coll_name = detect_collection(db)
    coll = db[coll_name]
    log(f"Using collection: {coll_name}")

    since = dt.datetime.utcnow() - dt.timedelta(days=args.days)
    since_naive = since.replace(tzinfo=None)
    log(f"Scanning videos published since {since_naive.isoformat()}")

    # --- Smart fetch supporting both string and Date ---
    docs = []
    since_str = since_naive.isoformat()[:10]
    for doc in coll.find({}, {"snippet.title": 1, "snippet.tags": 1, "snippet.publishedAt": 1}):
        pub = doc.get("snippet", {}).get("publishedAt")
        if not pub:
            continue
        # Case 1: ISODate
        if hasattr(pub, "isoformat"):
            if pub.replace(tzinfo=None) >= since_naive:
                docs.append(doc)
        # Case 2: String timestamp
        elif isinstance(pub, str):
            try:
                pub_dt = dt.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if pub_dt.replace(tzinfo=None) >= since_naive:
                    docs.append(doc)
            except Exception:
                # fallback compare date prefix
                if pub[:10] >= since_str:
                    docs.append(doc)

    count_docs = len(docs)
    if count_docs == 0:
        log("⚠️  No documents found in that period.")
        return

    # --- Keyword extraction ---
    keywords = Counter()
    for doc in docs:
        snip = doc.get("snippet", {})
        title = snip.get("title", "")
        tags = snip.get("tags", [])
        words = clean_text(title)
        if isinstance(tags, list):
            for tag in tags:
                words.extend(clean_text(tag))
        for w in words:
            keywords[w] += 1

    if not keywords:
        log("⚠️  No keywords extracted.")
        return

    top = keywords.most_common(100)
    weights = {}
    for i, (k, v) in enumerate(top):
        w = 1.6 - 0.6 * (i / max(len(top)-1, 1))
        weights[k] = round(float(np.clip(w, 1.0, 1.6)), 3)

    os.makedirs(args.outdir, exist_ok=True)
    outpath = os.path.join(args.outdir, "local_trending_weights.json")
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump({
            "run_ts": dt.datetime.utcnow().isoformat() + "Z",
            "source": "local",
            "days": args.days,
            "weights": weights
        }, f, ensure_ascii=False, indent=2)

    log(f"✅ Local trend weights saved -> {outpath} ({len(weights)} keywords from {count_docs} videos)")

if __name__ == "__main__":
    main()
