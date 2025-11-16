from pymongo import MongoClient
c = MongoClient("mongodb://localhost:27017/")
print(c.list_database_names())
