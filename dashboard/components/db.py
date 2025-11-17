# dashboard/components/db.py
import os
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError
from dotenv import load_dotenv
import streamlit as st


def _load_env():
    """
    Load environment variables from:
    1. /home/ytscan/.env (VPS)
    2. ./.env (local dev)
    """
    home_env = Path("/home/ytscan/.env")
    if home_env.exists():
        load_dotenv(home_env)

    # Local .env (for dev / VSCode)
    load_dotenv()


def _resolve_db_name() -> str:
    """
    Try MONGO_DB first (chuẩn mới),
    fallback sang DB_NAME, cuối cùng là 'ytscan'.
    """
    return (
        os.getenv("MONGO_DB")
        or os.getenv("DB_NAME")
        or "ytscan"
    )


@st.cache_resource
def get_client() -> MongoClient:
    """
    Create a single cached MongoClient for the whole dashboard.
    Raises a Streamlit error if connection fails.
    """
    _load_env()
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        # Health check: fail early if Mongo is down or URI is wrong
        client.admin.command("ping")
        return client
    except ServerSelectionTimeoutError as e:
        st.error(f"❌ Cannot connect to MongoDB: {e}")
        raise
    except Exception as e:
        st.error(f"❌ Unexpected MongoDB error: {e}")
        raise


def get_db():
    """
    Return the main database used by the dashboard.
    """
    db_name = _resolve_db_name()
    return get_client()[db_name]


def get_collection(name: str):
    """
    Generic helper to get any collection by name.
    Example:
        videos = get_collection("videos")
    """
    return get_db()[name]


# Convenience helpers for common collections
def get_videos_collection():
    return get_collection("videos")


def get_processed_videos_collection():
    return get_collection("processed_videos")


def get_dashboard_summary_collection():
    return get_collection("dashboard_summary")
