"""Configuration constants and settings."""
import os
from dotenv import load_dotenv

load_dotenv()

# MongoDB Configuration
MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    raise ValueError("MONGODB_URL environment variable is not set")

MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "3000"))
MAX_POOL_SIZE = int(os.getenv("MONGODB_MAX_POOL_SIZE", "50"))
MIN_POOL_SIZE = int(os.getenv("MONGODB_MIN_POOL_SIZE", "5"))

# Collection Names
COLLECTIONS = {
    'archives': {'db': 'archives', 'coll': 'archives', 'date_field': 'inserted_time'},
    'credentials': {'db': 'infostealer', 'coll': 'credentials', 'date_field': 'harvest_date'},
    'alerts': {'db': 'backoffice', 'coll': 'alerts', 'date_field': 'created_date'},
    'organizations': {'db': 'backoffice', 'coll': 'organizations', 'date_field': 'created_at'}
}
