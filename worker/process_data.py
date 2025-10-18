#!/usr/bin/env python3
# process_data.py (v6) — v5 + dashboard_overview.json (total/processed/pending)
from __future__ import annotations

import argparse
import json
import sys
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from pathlib import Path

try:
    from pymongo import MongoClient, UpdateOne
except Exception:
    MongoClient = None  # optional
    UpdateOne = None

# optional dotenv loader
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(dotenv_path=None): ...

def default_plan_minutes() -> List[int]:
    plan: List[int] = []
    plan += list(range(5,  120 + 1, 5))    # 0–2h: every 5 min
    plan += list(range(135, 360 + 1, 15))  # 2–6h: every 15 min
    plan += list(range(390, 720 + 1, 30))  # 6–12h: every 30 min
    plan += list(range(780, 1440 + 1, 60)) # 12–24h: every 60 min
    return plan

PLAN_MINUTES = default_plan_minutes()
HORIZONS = [60, 180, 360, 720, 1440]  # 1h,3h,6h,12h,24h
CEIL_TOLERANCE_MIN = 30

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
    v = s.get('viewCount',0) or 0
    try:
        v = int(v)
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
    return Snapshot(ts=ts, viewCount=max(0,v), likeCount=lk, commentCount=cm)

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

def summarize_video(doc:Dict[str,Any])->Dict[str,Any]:
    vid=str(doc.get('_id') or doc.get('video_id') or '')
    status=(doc.get('tracking') or {}).get('status')
    pub=parse_iso(((doc.get('snippet') or {}).get('publishedAt')))
    raw=doc.get('stats_snapshots') or []
    snaps=[s for s in (coerce_snap(x) for x in raw) if s]
    snaps.sort(key=lambda x:x.ts)
    enforce_non_decreasing(snaps)
    last_ts=snaps[-1].ts if snaps else None

    horizons_out={}
    completed_horizons: List[int] = []
    for h in HORIZONS:
        snap_h,method=floor_ceil_value(snaps,pub,h)
        cov=coverage_ratio(snaps,pub,h)
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

    return {
        "video_id": vid,
        "status": status,
        "published_at": iso(pub),
        "n_snapshots": len(snaps),
        "last_snapshot_ts": iso(last_ts),
        "completed_horizons": completed_horizons,
        "n_completed_horizons": len(completed_horizons),
        "horizons": horizons_out,
    }

def build_dashboard_summary(rows:List[Dict[str,Any]])->List[Dict[str,Any]]:
    out=[]
    for r in rows:
        hz=r.get("horizons",{})
        out.append({
            "video_id":r["video_id"],
            "status":r.get("status"),
            "published_at":r.get("published_at"),
            "n_snapshots":r.get("n_snapshots"),
            "last_snapshot_ts":r.get("last_snapshot_ts"),
            "reached_h1":hz.get("60",{}).get("value_method") in ("floor","ceil"),
            "reached_h3":hz.get("180",{}).get("value_method") in ("floor","ceil"),
            "reached_h6":hz.get("360",{}).get("value_method") in ("floor","ceil"),
            "reached_h12":hz.get("720",{}).get("value_method") in ("floor","ceil"),
            "reached_h24":hz.get("1440",{}).get("value_method") in ("floor","ceil"),
            "coverage_1h":hz.get("60",{}).get("coverage_ratio"),
            "coverage_3h":hz.get("180",{}).get("coverage_ratio"),
            "coverage_6h":hz.get("360",{}).get("coverage_ratio"),
            "coverage_12h":hz.get("720",{}).get("coverage_ratio"),
            "coverage_24h":hz.get("1440",{}).get("coverage_ratio"),
            "n_completed_horizons": len(r.get("completed_horizons", []))
        })
    return out

def read_from_mongo(uri:str,db_name:str,coll:str, query:dict|None=None):
    if MongoClient is None:
        raise RuntimeError("pymongo not installed")
    client=MongoClient(uri)
    db=client[db_name]
    q = query or {}
    print(f"🔍 Using query filter: {json.dumps(q, ensure_ascii=False)}")
    cur=db[coll].find(q,projection={"_id":1,"snippet.publishedAt":1,"tracking.status":1,"stats_snapshots":1})
    for d in cur:
        yield d

def read_from_mongo_unprocessed(uri:str, db_name:str, src_coll:str,
                                processed_coll:str, query:dict|None=None):
    """Stream only NOT-YET-PROCESSED docs.
    Join: stringified source _id -> processed.video_id.
    Defaults to tracking.status == "complete" if not specified.
    """
    if MongoClient is None:
        raise RuntimeError("pymongo not installed")
    client = MongoClient(uri)
    db = client[db_name]
    q = query or {}
    if "tracking.status" not in q:
        q["tracking.status"] = "complete"

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
            "tracking.status": 1,
            "stats_snapshots": 1
        }},
    ]
    print("🔍 Using server-side filter (skip processed) with pipeline:\n" + json.dumps(pipeline, ensure_ascii=False, indent=2))
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

def upsert_to_mongo(uri:str, db_name:str, coll_name:str, rows:List[Dict[str,Any]], key:str="video_id"):
    if MongoClient is None or UpdateOne is None:
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
        ops.append(UpdateOne({key: r[key]}, {"$set": r}, upsert=True))
    if ops:
        res = coll.bulk_write(ops, ordered=False)
        print(f"   ↳ {coll_name}: upserted={res.upserted_count}, modified={res.modified_count}")
    else:
        print(f"   ↳ {coll_name}: nothing to upsert")

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

def main():
    ap=argparse.ArgumentParser(description="Process YouTube tracker docs into JSON outputs.")
    ap.add_argument("--mongo-uri")
    ap.add_argument("--db", default=None)
    ap.add_argument("--collection", default=None)
    ap.add_argument("--input-json")
    ap.add_argument("--out-processed", default="processed_videos.json")
    ap.add_argument("--out-summary", default="dashboard_summary.json")
    ap.add_argument("--to-mongo", action="store_true", help="(Optional) Explicitly upsert outputs into Mongo (default: ON)")
    ap.add_argument("--no-mongo", action="store_true", help="Disable upserting outputs into Mongo")
    ap.add_argument("--query", help="MongoDB query as JSON string, e.g. '{\"tracking.status\":\"complete\"}'")
    ap.add_argument("--out-coll-processed", default="processed_videos", help="Collection for processed output")
    ap.add_argument("--out-coll-summary", default="dashboard_summary", help="Collection for dashboard summary")
    ap.add_argument("--skip-processed", default="true",
                    help="Skip documents already present in processed collection (true/false, default: true)" )
    ap.add_argument("--processed-source-coll", default=None,
                    help="Collection to check for already processed rows. Defaults to --out-coll-processed")
    args=ap.parse_args()

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
            print(f"✅ Auto-detected DB: {args.db}")
    if not args.collection:
        args.collection="videos"

    if not args.mongo_uri and not args.input_json:
        print("ERROR: Provide --mongo-uri or --input-json",file=sys.stderr)
        sys.exit(2)

    # Interpret skip flag
    skip_processed = _boolish(args.skip_processed)

    # Choose processed-source-coll default
    if not args.processed_source_coll:
        args.processed_source_coll = args.out_coll_processed  # usually "processed_videos"

    # Build query if provided
    query_dict = None
    if args.query:
        try:
            query_dict = json.loads(args.query)
        except Exception as e:
            print(f"ERROR: --query must be valid JSON. {e}", file=sys.stderr)
            sys.exit(4)

    # Decide data source
    if args.mongo_uri:
        if skip_processed:
            docs = read_from_mongo_unprocessed(
                args.mongo_uri, args.db, args.collection,
                processed_coll=args.processed_source_coll,
                query=query_dict
            )
        else:
            # fallback: plain find (you can still pass --query)
            if query_dict is None:
                query_dict = {"tracking.status": "complete"}
            docs = read_from_mongo(args.mongo_uri, args.db, args.collection, query=query_dict)
    else:
        docs = read_from_json(args.input_json)

    processed=[]
    for i,d in enumerate(docs,1):
        try:
            processed.append(summarize_video(d))
            if i%500==0:
                print(f"Processed {i} videos...",file=sys.stderr)
        except Exception as e:
            print(f"Skip doc due to error: {e}",file=sys.stderr)

    with open(args.out_processed,"w",encoding="utf-8") as f:
        json.dump(processed,f,ensure_ascii=False,indent=2)
    summary=build_dashboard_summary(processed)
    with open(args.out_summary,"w",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)
    print(f"\n✅ Wrote {args.out_processed} ({len(processed)} rows)")
    print(f"✅ Wrote {args.out_summary} ({len(summary)} rows)")

    # Optional: upsert outputs back to Mongo (default ON)
    do_push = True
    if args.no_mongo:
        do_push = False
    # Backward-compat: if user passed --to-mongo explicitly, still push
    if args.to_mongo:
        do_push = True

    if do_push:
        if not args.mongo_uri:
            print("⚠️  Skipping Mongo upsert: no MONGO_URI provided (use .env or --mongo-uri).", file=sys.stderr)
        elif not args.db:
            print("⚠️  Skipping Mongo upsert: could not detect DB name from URI. Provide --db explicitly.", file=sys.stderr)
        else:
            print("⏫ Upserting outputs into Mongo...")
            upsert_to_mongo(args.mongo_uri, args.db, args.out_coll_processed, processed, key="video_id")
            upsert_to_mongo(args.mongo_uri, args.db, args.out_coll_summary, summary, key="video_id")
            print("✅ Done upserting to Mongo.")

    # ---- NEW: create dashboard_overview.json with counts ----
    try:
        overview = {
            "total_videos": None,
            "processed_videos": None,
            "pending_videos": None,
            "timestamp": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z")
        }
        if args.mongo_uri and MongoClient is not None and args.db:
            client = MongoClient(args.mongo_uri)
            db = client[args.db]
            total_videos = db[args.collection].count_documents({})
            processed_count = db[args.out_coll_processed].count_documents({})
            pending = max(total_videos - processed_count, 0)
            overview.update({
                "total_videos": total_videos,
                "processed_videos": processed_count,
                "pending_videos": pending
            })
        else:
            # Fallback when using JSON input: infer processed from current run
            overview.update({
                "total_videos": None,
                "processed_videos": len(processed),
                "pending_videos": None
            })
        with open("dashboard_overview.json", "w", encoding="utf-8") as f:
            json.dump(overview, f, ensure_ascii=False, indent=2)
        print("📊 Dashboard overview saved → dashboard_overview.json")
        print(json.dumps(overview, indent=2))
    except Exception as e:
        print(f"⚠️  Failed to write dashboard_overview.json: {e}", file=sys.stderr)

if __name__=="__main__":
    main()