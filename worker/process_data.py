#!/usr/bin/env python3
# process_data.py (v7.3-lean, segment-based)
# -------------------------------------------------------------
# - Removes dashboard/summary/overview generation
# - Drops 'just_completed' state; only 'tracking' or 'complete'
# - Keeps skip-processed logic (reprocess TRACKING + NEW when enabled)
# - Adds duration_bucket fallback from durationSec when missing
# - snapshot_features: v_slope_mean/max/std, v_accel_mean, time_first_1k/10k
# - Extended per-horizon (3h,6h,12h,24h) ON by default — segment-based
#   Each horizon includes: v_slope_mean/max/std, v_accel_mean, coverage_ratio,
#   min_view, max_view, view_range, low_activity, plateau (computed from segment)

from __future__ import annotations

import argparse
import json
import sys
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from pathlib import Path
import math
import itertools

# Optional pymongo imports
try:
    from pymongo import MongoClient, UpdateOne, ReplaceOne
except Exception:
    MongoClient = None
    UpdateOne = None
    ReplaceOne = None

# Optional dotenv loader
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(dotenv_path=None):
        ...

# ------------------------- Plan & constants -------------------------

def default_plan_minutes() -> List[int]:
    plan: List[int] = []
    plan += list(range(5, 120 + 1, 5))     # 0–2h: every 5 min
    plan += list(range(135, 360 + 1, 15))  # 2–6h: every 15 min
    plan += list(range(390, 720 + 1, 30))  # 6–12h: every 30 min
    plan += list(range(780, 1440 + 1, 60)) # 12–24h: every 60 min
    return plan

PLAN_MINUTES = default_plan_minutes()
HORIZONS = [60, 180, 360, 720, 1440]  # 1h,3h,6h,12h,24h
CEIL_TOLERANCE_MIN = 30

# Extended features default: ON (disable with --no-extended-features)
DO_EXTENDED = True

# --------------------------- Utilities -----------------------------

def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc)
    except Exception:
        return None

def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z') if dt else None

@dataclass
class Snapshot:
    ts: datetime
    viewCount: int
    likeCount: Optional[int] = None
    commentCount: Optional[int] = None

def coerce_snap(s: Dict[str,Any]) -> Optional[Snapshot]:
    ts = parse_iso(s.get('ts'))
    if not ts:
        return None
    try:
        v = int(s.get('viewCount', 0) or 0)
    except Exception:
        v = 0
    lk = s.get('likeCount'); cm = s.get('commentCount')
    try:
        lk = int(lk) if lk is not None else None
    except Exception:
        lk = None
    try:
        cm = int(cm) if cm is not None else None
    except Exception:
        cm = None
    return Snapshot(ts=ts, viewCount=max(0, v), likeCount=lk, commentCount=cm)

def expected_count_up_to(h:int)->int:
    return sum(1 for m in PLAN_MINUTES if m<=h)

def enforce_non_decreasing(snaps:List[Snapshot])->None:
    vmax=0
    for s in sorted(snaps,key=lambda x:x.ts):
        if s.viewCount<vmax:
            s.viewCount=vmax
        vmax=s.viewCount

def floor_ceil_value(snaps:List[Snapshot], pub:Optional[datetime], h:int):
    if not pub:
        return (None,'missing')
    cutoff=pub+timedelta(minutes=h)
    snaps_sorted=sorted(snaps,key=lambda x:x.ts)
    floor=None
    for s in snaps_sorted:
        if s.ts<=cutoff:
            floor=s
        else:
            break
    if floor:
        return (floor,'floor')
    for s in snaps_sorted:
        if s.ts>cutoff and (s.ts-cutoff)<=timedelta(minutes=CEIL_TOLERANCE_MIN):
            return (s,'ceil')
    return (None,'missing')

def coverage_ratio(snaps:List[Snapshot], pub:Optional[datetime], h:int)->float:
    if not pub:
        return 0.0
    cutoff=pub+timedelta(minutes=h)
    avail=sum(1 for s in snaps if s.ts<=cutoff)
    exp=expected_count_up_to(h)
    return round(avail/max(exp,1),6)

# ---- helpers for per-horizon segment logic ----

def _views_at_cutoff(snaps: List[Snapshot], published: Optional[datetime], minutes: int) -> int:
    """Return viewCount at cutoff=published+minutes using floor/ceil tolerance."""
    if not published:
        return 0
    s, _m = floor_ceil_value(snaps, published, minutes)
    return int(s.viewCount) if s else 0

def _is_plateau_segment(snaps: List[Snapshot], start_ts: datetime, end_ts: datetime,
                        last_n: int = 3, threshold: int = 0) -> bool:
    """Plateau computed only within (start_ts, end_ts] segment."""
    seg = [max(0, int(s.viewCount)) for s in sorted(snaps, key=lambda x: x.ts)
           if s.ts > start_ts and s.ts <= end_ts]
    if len(seg) < last_n + 1:
        return False
    inc = [seg[i] - seg[i-1] for i in range(1, len(seg))]
    tail = inc[-last_n:]
    return all(d <= threshold for d in tail)

# ---------------- v7: snapshot feature helpers ----------------

def _hours_since(a: datetime, b: datetime) -> float:
    return max((a - b).total_seconds() / 3600.0, 0.0)

def _slope_stats(xs: List[float], ys: List[int]) -> Tuple[float,float,float,float]:
    """Return mean_slope, max_slope, std_slope, accel_mean. Safe defaults=0.0."""
    if len(xs) < 2:
        return 0.0, 0.0, 0.0, 0.0
    slopes: List[float] = []
    for i in range(1, len(xs)):
        dx = xs[i] - xs[i-1]
        dy = ys[i] - ys[i-1]
        if dx <= 0:
            continue
        slopes.append(dy / dx)
    if not slopes:
        return 0.0, 0.0, 0.0, 0.0
    mean_slope = sum(slopes) / len(slopes)
    max_slope = max(slopes)
    var = sum((s - mean_slope) ** 2 for s in slopes) / max(len(slopes)-1, 1)
    std_slope = math.sqrt(var)
    accs = [slopes[i] - slopes[i-1] for i in range(1, len(slopes))]
    accel_mean = (sum(accs)/len(accs)) if accs else 0.0
    return mean_slope, max_slope, std_slope, accel_mean

def compute_snapshot_features(snaps: List[Snapshot], published: Optional[datetime]) -> Dict[str, Optional[float]]:
    out = {
        "v_slope_mean": 0.0,
        "v_slope_max": 0.0,
        "v_slope_std": 0.0,
        "v_accel_mean": 0.0,
        "time_first_1k": 0.0,
        "time_first_10k": 0.0,
    }
    if not snaps or not published:
        return out

    srt = sorted(snaps, key=lambda x: x.ts)
    xs = [_hours_since(s.ts, published) for s in srt]
    ys = [max(0, int(s.viewCount)) for s in srt]

    mean_slope, max_slope, std_slope, accel_mean = _slope_stats(xs, ys)
    out.update({
        "v_slope_mean": round(mean_slope, 6),
        "v_slope_max": round(max_slope, 6),
        "v_slope_std": round(std_slope, 6),
        "v_accel_mean": round(accel_mean, 6),
    })

    def _time_to_threshold(th: int) -> float:
        for x, y in zip(xs, ys):
            if y >= th:
                return round(x, 1)  # 1 decimal only for time-first metrics
        return 0.0

    out["time_first_1k"] = _time_to_threshold(1_000)
    out["time_first_10k"] = _time_to_threshold(10_000)
    return out

def _extended_per_horizon(snaps: List[Snapshot], published: Optional[datetime],
                          horizon_min: int, prev_horizon_min: int) -> Dict[str, float | bool | int]:
    """
    Per-horizon features for segment (prev_horizon, horizon]:
      - v_slope_mean/max/std, v_accel_mean  (computed on snapshots within the segment)
      - coverage_ratio (to H, unchanged)
      - min_view = views@prev_horizon, max_view = views@horizon, view_range = delta
      - low_activity (based on segment's max_view & view_range)
      - plateau (increments only within the segment)
    """
    base: Dict[str, float | bool | int] = {
        "v_slope_mean": 0.0, "v_slope_max": 0.0, "v_slope_std": 0.0,
        "v_accel_mean": 0.0, "coverage_ratio": 0.0,
        "min_view": 0, "max_view": 0, "view_range": 0,
        "low_activity": True, "plateau": False,
    }
    if not published:
        return base

    start_ts = published + timedelta(minutes=prev_horizon_min)
    end_ts   = published + timedelta(minutes=horizon_min)

    # coverage to H: full-horizon coverage (unchanged)
    base["coverage_ratio"] = coverage_ratio(snaps, published, horizon_min)

    # snapshots strictly within the segment (start_ts, end_ts]
    subs = [s for s in snaps if s.ts > start_ts and s.ts <= end_ts]
    if subs:
        xs = [(s.ts - published).total_seconds() / 3600.0 for s in subs]
        ys = [max(0, int(s.viewCount)) for s in subs]
        m, mx, sd, acc = _slope_stats(xs, ys)
        base["v_slope_mean"] = round(m, 6)
        base["v_slope_max"]  = round(mx, 6)
        base["v_slope_std"]  = round(sd, 6)
        base["v_accel_mean"] = round(acc, 6)

    # boundary values → segment min/max/range
    v_prev = _views_at_cutoff(snaps, published, prev_horizon_min)
    v_curr = _views_at_cutoff(snaps, published, horizon_min)
    delta  = max(0, v_curr - v_prev)

    base["min_view"]   = v_prev
    base["max_view"]   = v_curr
    base["view_range"] = delta

    # segment low-activity rule
    base["low_activity"] = (v_curr < 200) or (delta < 50)

    # plateau only within segment
    base["plateau"] = _is_plateau_segment(snaps, start_ts, end_ts, last_n=3, threshold=0)
    return base

def classify_growth_phase(hz: Dict[str, Any]) -> Optional[str]:
    try:
        v6 = hz.get("360", {}).get("views") or 0
        v12 = hz.get("720", {}).get("views") or 0
        v24 = hz.get("1440", {}).get("views") or 0
        dv_6_12 = (v12 - v6)
        dv_12_24 = (v24 - v12)
        if v6 == 0 and v12 == 0 and v24 == 0:
            return "flat"
        if dv_6_12 > 0 and dv_12_24 > 0:
            if dv_12_24 >= 1.5 * max(dv_6_12, 1):
                return "early-burst"
            return "steady"
        if v24 <= 5_000:
            return "flat"
        return "steady"
    except Exception:
        return None

# --------------------------- Core summarize -------------------------

def summarize_video(doc:Dict[str,Any])->Dict[str,Any]:
    vid=str(doc.get('_id') or doc.get('video_id') or '')
    status=(doc.get('tracking') or {}).get('status')
    snippet = (doc.get('snippet') or {})
    pub=parse_iso(snippet.get('publishedAt'))

    # Duration bucket fallback from durationSec if missing
    dur_bucket = snippet.get("lengthBucket") or snippet.get("durationBucket")
    dur_sec = snippet.get("durationSec")
    if not dur_bucket and isinstance(dur_sec, (int, float)):
        s = int(max(dur_sec, 0))
        if s < 61:
            dur_bucket = "shorts"
        elif s <= 240:
            dur_bucket = "short"
        elif s <= 1200:
            dur_bucket = "medium"
        else:
            dur_bucket = "long"

    raw=doc.get('stats_snapshots') or []
    snaps=[s for s in (coerce_snap(x) for x in raw) if s]
    snaps.sort(key=lambda x:x.ts)
    enforce_non_decreasing(snaps)
    last_ts=snaps[-1].ts if snaps else None

    horizons_out={}
    completed_horizons: List[int] = []
    cov_values: List[float] = []
    for h in HORIZONS:
        snap_h,method=floor_ceil_value(snaps,pub,h)
        cov=coverage_ratio(snaps,pub,h)
        cov_values.append(cov)
        horizons_out[str(h)] = {
            "views": snap_h.viewCount if snap_h else None,
            "likes": snap_h.likeCount if snap_h else None,
            "comments": snap_h.commentCount if snap_h else None,
            "value_method": method,
            "coverage_ratio": cov,
            "n_expected": expected_count_up_to(h),
            "n_available": int(round(cov*max(expected_count_up_to(h),1))),
        }
        if method in ("floor","ceil"):
            completed_horizons.append(h)

    coverage_score = None
    if cov_values:
        coverage_score = round(sum(cov_values)/len(cov_values), 6)

    snap_feats = compute_snapshot_features(snaps, pub)

    # extended features per horizon (3h,6h,12h,24h) — segment-based
    if DO_EXTENDED:
        ext: Dict[str, Dict[str, float | bool | int]] = {}
        prev_map = {180: 60, 360: 180, 720: 360, 1440: 720}
        for h in (180, 360, 720, 1440):
            ext[str(h)] = _extended_per_horizon(snaps, pub, h, prev_map[h])
        snap_feats["extended"] = ext

    growth_phase = classify_growth_phase(horizons_out)

    ml_flags = {
        "likely_viral": False,
        "score": 0.0,
        "viral_confirmed": False
    }

    return {
        "video_id": vid,
        "status": status,
        "published_at": iso(pub),
        "n_snapshots": len(snaps),
        "last_snapshot_ts": iso(last_ts),
        "completed_horizons": completed_horizons,
        "horizons": horizons_out,
        "coverage_score": coverage_score,
        "growth_phase": growth_phase,
        "snapshot_features": snap_feats,
        "ml_flags": ml_flags,
    }

# --------------------------- IO helpers -----------------------------

def read_from_mongo(uri:str,db_name:str,coll:str, query:dict|None=None):
    if MongoClient is None:
        raise RuntimeError("pymongo not installed")
    client=MongoClient(uri)
    db=client[db_name]
    q = query or {}
    print(f"🔍 Using query filter: {json.dumps(q, ensure_ascii=False)}")
    cur=db[coll].find(
        q,
        projection={
            "_id":1,
            "snippet.publishedAt":1,
            "snippet.categoryId":1,
            "snippet.durationISO":1,
            "snippet.durationSec":1,
            "snippet.lengthBucket":1,
            "tracking.status":1,
            "source.regionCode":1,
            "source.region":1,
            "source.query":1,
            "source.querySeed":1,
            "stats_snapshots":1
        }
    )
    for d in cur:
        yield d

def read_from_mongo_unprocessed(uri:str, db_name:str, src_coll:str, processed_coll:str, query:dict|None=None):
    """Stream NOT-YET-PROCESSED docs (skip processed), but always include TRACKING set from query."""
    if MongoClient is None:
        raise RuntimeError("pymongo not installed")
    client = MongoClient(uri)
    db = client[db_name]
    q = query or {}
    if "tracking.status" not in q:
        q["tracking.status"] = {"$in": ["complete", "tracking"]}
    pipeline = [
        {"$match": q},
        {"$addFields": {"_id_str": {"$toString": "$_id"}}},
        {"$lookup": {
            "from": processed_coll,
            "localField": "_id_str",
            "foreignField": "video_id",
            "as": "p"
        }},
        {"$match": {"p": {"$eq": []}}},
        {"$project": {
            "_id": 1,
            "snippet.publishedAt": 1,
            "snippet.categoryId":1,
            "snippet.durationISO":1,
            "snippet.durationSec":1,
            "snippet.lengthBucket":1,
            "tracking.status": 1,
            "source.regionCode":1,
            "source.region":1,
            "source.query":1,
            "source.querySeed":1,
            "stats_snapshots": 1
        }},
    ]
    print(
        "🔍 Using server-side filter (skip processed) with pipeline:\n"
        + json.dumps(pipeline, ensure_ascii=False, indent=2)
    )
    cur = db[src_coll].aggregate(pipeline, allowDiskUse=True)
    for d in cur:
        yield d

def read_from_json(path:str):
    if path.lower().endswith((".ndjson",".jsonl")):
        with open(path,"r",encoding="utf-8") as fh:
            for line in fh:
                line=line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    else:
        with open(path,"r",encoding="utf-8") as fh:
            data=json.load(fh)
            if isinstance(data,list):
                yield from data
            elif isinstance(data,dict):
                yield data

def upsert_to_mongo(uri:str, db_name:str, coll_name:str, rows:List[Dict[str,Any]], key:str="video_id", use_replace: bool = False):
    if MongoClient is None or (UpdateOne is None and ReplaceOne is None):
        raise RuntimeError("pymongo is required for --to-mongo")
    client = MongoClient(uri)
    db = client[db_name]
    coll = db[coll_name]
    try:
        coll.create_index(key, unique=True)
    except Exception:
        pass

    ops = []
    for r in rows:
        if key not in r:
            continue
        if use_replace and ReplaceOne is not None:
            ops.append(ReplaceOne({key: r[key]}, r, upsert=True))
        else:
            ops.append(UpdateOne({key: r[key]}, {"$set": r}, upsert=True))

    if ops:
        res = coll.bulk_write(ops, ordered=False)
        up = getattr(res, "upserted_count", 0)
        mod = getattr(res, "modified_count", 0)
        print(f" ↳ {coll_name}: upserted={up}, modified={mod}, strategy={'replace' if use_replace else 'set'}")
    else:
        print(f" ↳ {coll_name}: nothing to upsert")

# --------------------------- CLI helpers ----------------------------

def detect_db_from_uri(uri:str)->Optional[str]:
    tail = uri.split("/")[-1]
    if not tail or tail.startswith("?"):
        return None
    return tail

def _boolish(v) -> bool:
    """Return False if v is in (0, false, no, off), True otherwise."""
    if v is None:
        return True
    s = str(v).strip().lower()
    return s not in ("0","false","no","off")

# ------------------------------- main -------------------------------

def main():
    global DO_EXTENDED

    ap=argparse.ArgumentParser(description="Process YouTube tracker docs into JSON outputs.")
    ap.add_argument("--mongo-uri")
    ap.add_argument("--db", default=None)
    ap.add_argument("--collection", default=None)
    ap.add_argument("--input-json")
    ap.add_argument("--out-processed", default="processed_videos.json")
    ap.add_argument("--to-mongo", action="store_true", help="Upsert outputs into Mongo (ON if flag present)")
    ap.add_argument("--no-mongo", action="store_true", help="Disable upserting outputs into Mongo")
    ap.add_argument("--query", help="MongoDB query as JSON string, e.g. '{\"tracking.status\":\"complete\"}'")
    ap.add_argument("--out-coll-processed", default="processed_videos", help="Collection for processed output")
    ap.add_argument("--skip-processed", default="true", help="Skip documents already present in processed collection (true/false, default: true)")
    ap.add_argument("--processed-source-coll", default=None, help="Collection checked for already processed rows. Defaults to --out-coll-processed")
    ap.add_argument("--out-dir", default=None, help="Directory to write output JSONs. Default: project root (parent of this script). Can also be set via env OUTPUT_DIR")

    # Extended features flags: default ON; allow disabling via --no-extended-features
    ap.add_argument("--extended-features", dest="extended_features", action="store_true", default=True,
                    help="Add per-horizon extended features (3h/6h/12h/24h). Default: ON")
    ap.add_argument("--no-extended-features", dest="extended_features", action="store_false",
                    help="Disable extended features (override default ON)")

    ap.add_argument("--refresh-existing", action="store_true", help="Replace existing documents (by video_id) instead of $set updating.")

    args=ap.parse_args()

    DO_EXTENDED = bool(args.extended_features)

    # === Auto load .env ===
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    elif Path(".env").exists():
        load_dotenv(dotenv_path=Path(".env").resolve())

    if not args.mongo_uri and os.getenv("MONGO_URI"):
        args.mongo_uri=os.getenv("MONGO_URI")
        print(f"✅ Using Mongo URI from .env: {args.mongo_uri}")

    if not args.db and args.mongo_uri:
        guess = detect_db_from_uri(args.mongo_uri)
        if guess:
            args.db=guess
        if args.db:
            print(f"✅ Auto-detected DB: {args.db}")

    if not args.collection:
        args.collection="videos"

    if not args.mongo_uri and not args.input_json:
        print("ERROR: Provide --mongo-uri or --input-json",file=sys.stderr)
        sys.exit(2)

    skip_processed = _boolish(args.skip_processed)

    if not args.processed_source_coll:
        args.processed_source_coll = args.out_coll_processed  # usually "processed_videos"

    query_dict = None
    if args.query:
        try:
            query_dict = json.loads(args.query)
        except Exception as e:
            print(f"ERROR: --query must be valid JSON. {e}", file=sys.stderr)
            sys.exit(4)

    # === Resolve out directory ===
    default_out_dir = Path(__file__).resolve().parents[1]
    env_out_dir = os.getenv("OUTPUT_DIR")
    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    elif env_out_dir:
        out_dir = Path(env_out_dir).expanduser().resolve()
    else:
        out_dir = default_out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    p_out_processed = (out_dir / args.out_processed).resolve()

    # Decide data source — include complete + tracking by default
    DEFAULT_STATUS_FILTER = {"$in": ["complete", "tracking"]}
    if query_dict is None:
        query_dict = {}
    if "tracking.status" not in query_dict:
        query_dict["tracking.status"] = DEFAULT_STATUS_FILTER

    print(f"🔧 Normalized query: {json.dumps(query_dict, ensure_ascii=False)}")

    if args.mongo_uri:
        if skip_processed:
            q_tracking = dict(query_dict)
            q_tracking["tracking.status"] = "tracking"
            docs_tracking = read_from_mongo(args.mongo_uri, args.db, args.collection, query=q_tracking)

            docs_new = read_from_mongo_unprocessed(
                args.mongo_uri, args.db, args.collection,
                processed_coll=args.processed_source_coll,
                query=query_dict
            )
            docs = itertools.chain(docs_tracking, docs_new)
            print("📦 Mode: skip-processed=true ⇒ reprocessing TRACKING + NEW only")
        else:
            docs = read_from_mongo(args.mongo_uri, args.db, args.collection, query=query_dict)
            print("📦 Mode: skip-processed=false ⇒ reprocessing ALL matched docs")
    else:
        docs = read_from_json(args.input_json)

    processed=[]
    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z")
    for i,d in enumerate(docs,1):
        try:
            r = summarize_video(d)
            r["processed_at"] = now_iso

            # only two states needed: complete or tracking
            st  = (r.get("status") or "").lower()
            r["processed_status"] = "complete" if st == "complete" else "tracking"

            processed.append(r)
            if i % 500 == 0:
                print(f"Processed {i} videos...", file=sys.stderr)
        except Exception as e:
            print(f"Skip doc due to error: {e}", file=sys.stderr)

    with open(p_out_processed,"w",encoding="utf-8") as f:
        json.dump(processed,f,ensure_ascii=False,indent=2)

    print(f"\n✅ Wrote {p_out_processed} ({len(processed)} rows)")

    # Optional: upsert outputs back to Mongo
    do_push = True
    if args.no_mongo:
        do_push = False
    if args.to_mongo:
        do_push = True

    if do_push:
        if not args.mongo_uri:
            print("⚠️ Skipping Mongo upsert: no MONGO_URI provided (use .env or --mongo-uri).", file=sys.stderr)
        elif not args.db:
            print("⚠️ Skipping Mongo upsert: could not detect DB name from URI. Provide --db explicitly.", file=sys.stderr)
        else:
            print("⏫ Upserting outputs into Mongo...")
            use_replace = bool(getattr(args, "refresh_existing", False))
            upsert_to_mongo(args.mongo_uri, args.db, args.out_coll_processed, processed, key="video_id", use_replace=use_replace)
            print("✅ Done upserting to Mongo.")

if __name__=="__main__":
    main()
