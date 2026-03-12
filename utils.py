"""Shared utilities for CLI scripts."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    print("Error: MONGODB_URL environment variable is not set")
    sys.exit(1)

MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "3000"))


def get_mongo_client():
    """Get MongoDB client connection."""
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
