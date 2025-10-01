from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from datetime import datetime, timezone
import os

app = FastAPI(title="YouTube Scanner API")
client = MongoClient(os.environ["MONGO_URI"])
db = client.get_database()

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

@app.get("/videos")
def list_videos(status: str | None = None, limit: int = 50):
    q = {}
    if status:
        q["tracking.status"] = status
    cur = db.videos.find(q).sort("snippet.publishedAt", -1).limit(limit)
    out = []
    for v in cur:
        v["_id"] = str(v["_id"])
        out.append(v)
    return out

@app.get("/video/{vid}")
def get_video(vid: str):
    v = db.videos.find_one({"_id": vid})
    if not v:
        raise HTTPException(404, "Video not found")
    v["_id"] = str(v["_id"])
    return v

@app.get("/tracking")
def tracking(limit: int = 50):
    cur = db.videos.find({"tracking.status":"tracking"}) \
                   .sort("tracking.next_poll_after", 1).limit(limit)
    return [{**v, "_id": str(v["_id"])} for v in cur]

@app.get("/complete")
def complete(limit: int = 50):
    cur = db.videos.find({"tracking.status":"complete"}) \
                   .sort("snippet.publishedAt", -1).limit(limit)
    return [{**v, "_id": str(v["_id"])} for v in cur]
