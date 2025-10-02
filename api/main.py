from fastapi import FastAPI, HTTPException, Query
from pymongo import MongoClient, ASCENDING, DESCENDING
from datetime import datetime, timezone
import os

app = FastAPI(title="YouTube Scanner API")

# Use env if available, else fall back to local Mongo
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ytscan")
client = MongoClient(MONGO_URI, tz_aware=True)
db = client.get_database()

def _build_query(
    status: str | None,
    q: str | None,
    channel_id: str | None,
    sort_field: str,
    since: str | None,
    before: str | None,
):
    query: dict = {}
    if status:
        query["tracking.status"] = status
    if q:
        query["snippet.title"] = {"$regex": q, "$options": "i"}
    if channel_id:
        query["snippet.channelId"] = channel_id

    time_filter = {}
    if since:
        time_filter["$gte"] = since
    if before:
        time_filter["$lte"] = before
    if time_filter:
        query[sort_field] = time_filter

    return query

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

@app.get("/videos")
def list_videos(
    status: str | None = Query(None, description="tracking.status filter"),
    limit: int = Query(50, ge=1, le=200),
    sort: str = Query("published", pattern="^(published|discovered)$",
                      description="Sort by 'published' or 'discovered'"),
    order: str = Query("desc", pattern="^(asc|desc)$",
                       description="Sort order 'asc' or 'desc'"),
    q: str | None = Query(None, description="Case-insensitive title search"),
    channel_id: str | None = Query(None, description="Filter by snippet.channelId"),
    since: str | None = Query(None, description="ISO timestamp lower-bound"),
    before: str | None = Query(None, description="ISO timestamp upper-bound"),
):
    sort_field = "snippet.publishedAt" if sort == "published" else "tracking.discovered_at"
    direction = DESCENDING if order == "desc" else ASCENDING

    query = _build_query(status, q, channel_id, sort_field, since, before)

    cur = (
        db.videos.find(query)
        .sort([(sort_field, direction), ("_id", direction)])
        .limit(limit)
    )
    out = []
    for v in cur:
        v["_id"] = str(v["_id"])  # ensure string
        out.append(v)
    return out

@app.get("/videos/count")
def videos_count(
    status: str | None = Query(None),
    q: str | None = Query(None),
    channel_id: str | None = Query(None),
    since: str | None = Query(None),
    before: str | None = Query(None),
    sort: str = Query("published", pattern="^(published|discovered)$"),
):
    sort_field = "snippet.publishedAt" if sort == "published" else "tracking.discovered_at"
    query = _build_query(status, q, channel_id, sort_field, since, before)
    return {"count": db.videos.count_documents(query)}

@app.get("/stats")
def stats():
    total = db.videos.count_documents({})
    tracking = db.videos.count_documents({"tracking.status": "tracking"})
    complete = db.videos.count_documents({"tracking.status": "complete"})
    last = db.videos.find({}, {"_id": 1, "tracking.discovered_at": 1}) \
                    .sort("tracking.discovered_at", DESCENDING) \
                    .limit(1)
    last_doc = next(last, None)
    last_discovered_at = None
    if last_doc and isinstance(last_doc, dict):
        last_discovered_at = last_doc.get("tracking", {}).get("discovered_at")
    return {
        "total": total,
        "tracking": tracking,
        "complete": complete,
        "last_discovered_at": last_discovered_at,
    }

@app.get("/video/{vid}")
def get_video(vid: str):
    v = db.videos.find_one({"_id": vid})
    if not v:
        raise HTTPException(404, "Video not found")
    v["_id"] = str(v["_id"])
    return v

@app.get("/tracking")
def tracking(limit: int = Query(50, ge=1, le=200)):
    cur = (
        db.videos.find({"tracking.status": "tracking"})
        .sort("tracking.next_poll_after", ASCENDING)
        .limit(limit)
    )
    return [{**v, "_id": str(v["_id"])} for v in cur]

@app.get("/complete")
def complete(limit: int = Query(50, ge=1, le=200)):
    cur = (
        db.videos.find({"tracking.status": "complete"})
        .sort("snippet.publishedAt", DESCENDING)
        .limit(limit)
    )
    return [{**v, "_id": str(v["_id"])} for v in cur]
