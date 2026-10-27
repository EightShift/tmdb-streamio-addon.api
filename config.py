import os
from dotenv import load_dotenv

load_dotenv()

VERSION = "1.0.0"
NAME = "🎬 TMDB Streamio"
PREFIX = "🎬"

HOST = os.getenv("HOST") or "0.0.0.0"
PORT = int(os.getenv("PORT", "8000"))

DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", 'en-US')
PUBLIC_URL = os.getenv('PUBLIC_URL', '').rstrip('/')

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_REGION = os.getenv("TMDB_REGION", None)

if not TMDB_API_KEY:
    raise ValueError("TMDB_API_KEY environment variable is required")

CACHE_DIR = os.getenv("CACHE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

CONTENT_TTL_HOURS = float(os.getenv("CONTENT_TTL_HOURS", "24"))
GENRES_TTL_DAYS = int(os.getenv("GENRES_TTL_DAYS", "30"))
MAX_COLLECTIONS = int(os.getenv("MAX_COLLECTIONS", "100"))
MIN_COLLECTION_PARTS = int(os.getenv("MIN_COLLECTION_PARTS", "2"))
COLLECTIONS_TTL_DAYS = int(os.getenv("COLLECTIONS_TTL_DAYS", "30"))
COLLECTIONS_SORTING_METHOD = os.getenv("COLLECTIONS_SORTING_METHOD", "avg").lower()
COLLECTIONS_SORTING_ATTRIBUTE = os.getenv("COLLECTIONS_SORTING_ATTRIBUTE", "vote_average*count").lower()

if COLLECTIONS_SORTING_METHOD not in ('avg', 'max'): raise ValueError("COLLECTIONS_SORTING_METHOD must be 'avg' or 'max'")
if COLLECTIONS_SORTING_ATTRIBUTE not in ('vote_average*count', 'popularity'): raise ValueError("COLLECTIONS_SORTING_ATTRIBUTE must be 'vote_average*count' or 'popularity'")

TMDB_RPS = int(os.getenv("TMDB_RPS", "40"))
