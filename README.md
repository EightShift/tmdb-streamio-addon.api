# TMDB Streamio Addon

A Stremio catalog add-on for discovering movies and TV series using data from [The Movie Database (TMDB)](https://www.themoviedb.org/). It provides localized catalogs, title and person search, movie collections, and detailed metadata including series episodes.

## Features

- Movie and series catalogs for **Popular**, **Top Rated**, and **New** titles, with genre filters.
- Search for movies and series by title or person. Person searches include matching credits.
- A movie collections catalog, ranked from TMDB collection data.
- Metadata lookup by IMDb ID (`tt...`) or TMDB ID (`tmdb:...`), including cast, directors/creators, ratings, artwork, and episode details.
- Localized manifests, catalogs, metadata, and installation pages. The available languages are listed in [`locales.csv`](locales.csv).
- In-memory request caching and persistent caches for TMDB genres and movie collections.

## Requirements

- Python 3.13
- A TMDB API key

Create an API key in your [TMDB account settings](https://www.themoviedb.org/settings/api). Keep the key private and do not commit it.

## Run locally

```bash
git clone <repository-url>
cd tmdb-streamio-addon.api
python -m venv .venv
```

Activate the virtual environment, then install dependencies and configure the API key:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:TMDB_API_KEY = "your_tmdb_api_key"
python application.py
```

```bash
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
export TMDB_API_KEY="your_tmdb_api_key"
python application.py
```

The server listens on `http://localhost:8000` by default. Open that URL to get the installation page, or add `http://localhost:8000/manifest.json` to Stremio.

You can also put settings in a `.env` file in the project directory:

```dotenv
TMDB_API_KEY=your_tmdb_api_key
HOST=0.0.0.0
PORT=8000
DEFAULT_LANGUAGE=en-US
```

## Run with Docker

```bash
docker build -t tmdb-streamio-addon .
docker run --name tmdb-streamio-addon \
  -p 8000:8000 \
  -e TMDB_API_KEY=your_tmdb_api_key \
  -v tmdb-streamio-addon-cache:/app/cache \
  tmdb-streamio-addon
```

The named volume preserves the genre and collection caches between container runs. For deployment behind a reverse proxy or on a public host, set `PUBLIC_URL` to the externally reachable base URL (for example, `https://addons.example.com`) so generated manifest and installation links use the correct address.

## Configuration

All settings are optional except `TMDB_API_KEY`.

| Variable | Default | Description |
| --- | --- | --- |
| `TMDB_API_KEY` | — | TMDB API key. Required. |
| `API_KEYS_ENABLED` | `false` | Require a key for all HTTP endpoints, including documentation. Accepts true/false, 1/0, yes/no, on/off. |
| `API_KEYS` | empty | Access keys separated by commas, semicolons, spaces or newlines. |
| `API_KEYS_FILE` | empty | Path to a UTF-8 file containing access keys. Relative paths use the server working directory. Combined with `API_KEYS`. |
| `HOST` | `0.0.0.0` | Host interface used by the built-in server. |
| `PORT` | `8000` | Port used by the built-in server. |
| `PUBLIC_URL` | empty | Public base URL used to build manifest and install links. Set this when deployed behind a proxy. |
| `DEFAULT_LANGUAGE` | `en-US` | Default language. Must match or share a language prefix with a locale in `locales.csv`. |
| `TMDB_REGION` | TMDB default | Optional region passed to TMDB. |
| `CACHE_DIR` | `./cache` | Directory for persistent genre and collection cache files. |
| `CONTENT_TTL_HOURS` | `24` | Lifetime of persistent content cache entries, in hours. |
| `GENRES_TTL_DAYS` | `30` | Lifetime of cached TMDB genres, in days. |
| `MAX_COLLECTIONS` | `100` | Maximum number of collections included in the catalog. |
| `MIN_COLLECTION_PARTS` | `2` | Minimum number of titles required for a collection. |
| `COLLECTIONS_TTL_DAYS` | `30` | Lifetime of cached collection data, in days. |
| `COLLECTIONS_SORTING_METHOD` | `avg` | Collection ranking method: `avg` or `max`. |
| `COLLECTIONS_SORTING_ATTRIBUTE` | `vote_average*count` | Ranking attribute: `vote_average*count` or `popularity`. |
| `TMDB_RPS` | `40` | TMDB requests-per-second limit used by the cache/warm-up code. |

## Add-on endpoints

### Optional API key authentication

These access keys are separate from `TMDB_API_KEY`. For example:

```dotenv
API_KEYS_ENABLED=true
API_KEYS=first-secret-key,second-secret-key
API_KEYS_FILE=./secrets/api-keys.txt
```

Either key source can be used on its own. The file accepts one key per line
or the same separators as `API_KEYS`; blank lines and lines starting with `#`
are ignored. Keys may contain letters, digits, `.`, `_`, `~`, and `-`.
The `secrets/` directory is ignored by Git. Create the file yourself; keys are
loaded at startup, so restart the server after changing them. When authentication
is enabled, a missing/unreadable file or an empty key set prevents startup.
When disabled, no key file is read and requests work without keys.

Send `X-API-Key: first-secret-key` or `Authorization: Bearer first-secret-key`.
Missing or invalid keys receive HTTP 401. For Stremio, open
`https://addons.example.com/key/first-secret-key/en-US/install` and use its
installation link. Generated links preserve the key, including language changes.
Use HTTPS and keep these URLs private: they contain the access key.

With Docker Compose, `./secrets` is mounted at `/app/secrets` (read-only).
Set `API_KEYS_FILE=/app/secrets/api-keys.txt` in `.env` to use that file.

- `/` or `/{language}/install` — installation page with a language selector.
- `/manifest.json` or `/{language}/manifest.json` — Stremio manifest.
- `/install-link` or `/{language}/install-link` — manifest and Stremio deep links as JSON.
- `/catalog/{movie|series}/{catalog-id}.json` — catalog results.
- `/meta/{movie|series}/{id}.json` — metadata lookup.
- `/icon.png` — add-on icon.

The language-specific routes accept the locale codes in `locales.csv`; for example, `/en-US/manifest.json`.

## Development

Run the application with `python application.py`. On startup, it loads genre and movie collection data from TMDB and warms the language registry. Logs are written to standard output.

## Attribution

This add-on uses TMDB data. It is not endorsed or certified by TMDB. See [TMDB's terms of use](https://www.themoviedb.org/terms-of-use).
