import os
from pymongo import MongoClient
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ytscan")
db = MongoClient(MONGO_URI).get_database()
docs = [
    {
        "_id":"DEMO123",
        "snippet":{"title":"Demo video 1","publishedAt":"2025-09-30T12:00:00Z"},
        "tracking":{"status":"tracking","discovered_at":"2025-10-01T00:00:00Z","next_poll_after":"2025-10-01T00:00:00Z","poll_count":0},
        "stats_snapshots":[],
        "ml_flags":{"likely_viral":False,"viral_confirmed":False,"score":0.0}
    },
    {
        "_id":"DEMO456",
        "snippet":{"title":"Old video","publishedAt":"2025-09-29T12:00:00Z"},
        "tracking":{"status":"complete","discovered_at":"2025-09-29T13:00:00Z","next_poll_after":None,"poll_count":5,"stop_reason":"age>=24h"},
        "stats_snapshots":[{"ts":"2025-09-29T13:00:00Z","viewCount":100,"likeCount":3,"commentCount":0}],
        "ml_flags":{"likely_viral":False,"viral_confirmed":False,"score":0.0}
    }
]
db.videos.insert_many(docs, ordered=False)
print("seeded", len(docs), "docs")
