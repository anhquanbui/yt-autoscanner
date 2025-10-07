# tools/make_indexes.py
import os
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
db = MongoClient(MONGO_URI).get_database()

db.videos.create_index([("tracking.status", 1), ("tracking.next_poll_after", 1)])
db.videos.create_index([("snippet.publishedAt", -1)])
db.videos.create_index([("source.regionCode", 1)])

print("Indexes created. Current indexes:")
for ix in db.videos.list_indexes():
    print(ix)