# dashboard/components/db.py
import os
from pymongo import MongoClient
from dotenv import load_dotenv
import streamlit as st

load_dotenv()

@st.cache_resource
def get_client():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    return MongoClient(uri)

def get_db():
    db_name = os.getenv("MONGO_DB", "ytscan")
    return get_client()[db_name]
