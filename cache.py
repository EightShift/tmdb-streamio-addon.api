import asyncio
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from tqdm import tqdm

import config
from logger import logger


CACHE_VERSION = 1

GENRES_CACHE_FILE = os.path.join(config.CACHE_DIR, 'genres.json')
KINDS = ('movie', 'series')

COLLECTIONS_CACHE_FILE = os.path.join(config.CACHE_DIR, 'collections.json')
EXPORT_URL = 'https://files.tmdb.org/p/exports/collection_ids_{date}.json.gz'
EXPORT_LOOKBACK_DAYS = 4 
MAX_FAILED_SHARE = 0.5

MAX_INFLIGHT = 32
RETRIES = 3


# ------------------------------------ main ------------------------------------

class RateLimiter:
    def __init__(self, rate: float):
        self._interval = 1.0 / rate
        self._next = 0.0

    async def acquire(self):
        now = time.monotonic()
        start = max(now, self._next)
        self._next = start + self._interval
        if start > now:
            await asyncio.sleep(start - now)


class Fetcher:
    def __init__(self):
        self._limiter = RateLimiter(config.TMDB_RPS)
        self._slots = asyncio.Semaphore(MAX_INFLIGHT)

    async def call(self, make_call):
        error = None
        for attempt in range(RETRIES):
            async with self._slots:
                await self._limiter.acquire()
                try:
                    return await make_call()
                except Exception as e:
                    error = e
            await asyncio.sleep(0.5 * 2 ** attempt)
        raise error


async def run_with_progress(items, worker, desc, unit):
    results, failed = {}, []
    with tqdm(total=len(items), desc=desc, unit=unit, dynamic_ncols=True, mininterval=1.0) as bar:
        async def one(item):
            try:
                results[item] = await worker(item)
            except Exception:
                failed.append(item)
            finally:
                bar.update(1)

        await asyncio.gather(*(one(i) for i in items))
    return results, failed


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)


# ------------------------------- cache -------------------------------

def ttl_cache(ttl: int, maxsize: int = 1000):
    def deco(fn):
        store: dict = {}

        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            hit = store.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            value = await fn(*args, **kwargs)
            if len(store) >= maxsize:
                store.clear()
            store[key] = (now, value)
            return value
        return wrapper
    return deco


# ------------------------------------ genres ------------------------------------

class GenresCache:
    def __init__(self, genres=None, built_at=0.0):
        self.genres: dict[str, dict[str, dict[int, str]]] = genres or {}
        self.built_at = built_at

    @classmethod
    def load(cls, path=GENRES_CACHE_FILE):
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            if raw.get('version') != CACHE_VERSION:
                return None
            genres = {
                lang: {kind: {int(i): n for i, n in items.items()} for kind, items in kinds.items()}
                for lang, kinds in raw['genres'].items()
            }
            return cls(genres, float(raw['built_at']))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning(f'[Genres] Cache {path} is unreadable and will be rebuilt: {e}')
            return None

    def save(self, path=GENRES_CACHE_FILE):
        save_json(path, {'version': CACHE_VERSION, 'built_at': self.built_at, 'genres': self.genres})

    def is_stale(self) -> bool:
        days = config.GENRES_TTL_DAYS
        return days > 0 and time.time() - self.built_at > days * 86400


async def load_or_build_genres_cache(api, languages) -> GenresCache:
    cache = GenresCache.load() or GenresCache()
    stale = bool(cache.genres) and cache.is_stale()
    todo = list(languages) if stale else [lang for lang in languages if lang not in cache.genres]
    if not todo:
        return cache

    fetcher = Fetcher()

    async def worker(lang):
        client = api(lang)
        movie = await fetcher.call(lambda: client.genres().movie())
        tv = await fetcher.call(lambda: client.genres().tv())
        return {
            'movie': {g.id: g.name for g in movie.genres or []},
            'series': {g.id: g.name for g in tv.genres or []},
        }

    logger.info(f'[Genres] Fetching genres: {len(todo)} languages, {len(todo) * 2} requests...')
    results, failed = await run_with_progress(todo, worker, 'Genres', 'lang')
    if failed:
        logger.warning(f'[Genres] Failed languages (will be retried on next start): {", ".join(map(str, failed))}')

    if results:
        cache.genres.update(results)
        if stale or not cache.built_at:
            cache.built_at = time.time()
        cache.save()
    return cache


# ---------------------------------- collections ----------------------------------

class CollectionsCache:
    def __init__(self, ranking=None, names=None, built_at=0.0, sorting=None, attribute=None):
        self.ranking: list[int] = ranking or []                    # id по убыванию популярности
        self.names: dict[str, dict[int, str]] = names or {}        # {язык: {id: название}}
        self.built_at = built_at
        self.sorting = sorting or config.COLLECTIONS_SORTING_METHOD   # каким методом посчитан рейтинг
        self.attribute = attribute or config.COLLECTIONS_SORTING_ATTRIBUTE

    @classmethod
    def load(cls, path=COLLECTIONS_CACHE_FILE):
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            if raw.get('version') != CACHE_VERSION:
                return None
            names = {lang: {int(i): n for i, n in items.items()} for lang, items in raw['names'].items()}
            return cls([int(i) for i in raw['ranking']], names, float(raw['built_at']), raw.get('sorting', 'avg'), raw.get('attribute', 'vote_average'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning(f'[Collections] Cache {path} is unreadable and will be rebuilt: {e}')
            return None

    def save(self, path=COLLECTIONS_CACHE_FILE):
        save_json(path, {'version': CACHE_VERSION, 'built_at': self.built_at, 'sorting': self.sorting, 'attribute': self.attribute, 'ranking': self.ranking, 'names': self.names})

    def is_stale(self) -> bool:
        if self.sorting != config.COLLECTIONS_SORTING_METHOD or self.attribute != config.COLLECTIONS_SORTING_ATTRIBUTE:
            return True
        days = config.COLLECTIONS_TTL_DAYS
        return days > 0 and time.time() - self.built_at > days * 86400


async def fetch_export_ids(session) -> list[int]:
    now = datetime.now(timezone.utc)
    last = None
    for back in range(EXPORT_LOOKBACK_DAYS):
        url = EXPORT_URL.format(date=(now - timedelta(days=back)).strftime('%m_%d_%Y'))
        try:
            async with session.get(url) as r:
                if r.status != 200:
                    last = f'HTTP {r.status} for {url}'
                    continue
                raw = await r.read()
        except Exception as e:
            last = f'{url}: {e}'
            continue
        lines = gzip.decompress(raw).decode('utf-8').splitlines()
        return [json.loads(line)['id'] for line in lines if line.strip()]

    raise RuntimeError(f'TMDB collections export is unavailable ({last})')


async def _build_ranking(session, api, fetcher, default_language) -> CollectionsCache:
    ids = await fetch_export_ids(session)
    logger.info(f'[Collections] Export: {len(ids)} collections. Ranking by {config.COLLECTIONS_SORTING_METHOD}[{config.COLLECTIONS_SORTING_ATTRIBUTE}] (~{len(ids) / config.TMDB_RPS / 60:.1f} min)...')
    client = api(default_language)

    async def worker(cid):
        res = await fetcher.call(lambda: client.collection(cid).details())

        rates = []
        if config.COLLECTIONS_SORTING_ATTRIBUTE == 'vote_average*count':
            rates = [p.vote_average * p.vote_count for p in res.parts if p.vote_average and p.vote_count]

        if config.COLLECTIONS_SORTING_ATTRIBUTE == 'popularity':
            rates = [p.popularity for p in res.parts if p.popularity]

        rate = 0.0
        if rates:
            if config.COLLECTIONS_SORTING_METHOD == 'max':
                rate = max(rates)
            elif config.COLLECTIONS_SORTING_METHOD == 'avg':
                rate = (sum(rates) / len(rates))

        return res.name, len(rates), rate

    results, failed = await run_with_progress(ids, worker, 'Collections: popularity', 'col')
    if not results or len(failed) > len(ids) * MAX_FAILED_SHARE:
        raise RuntimeError(f'Too many TMDB failures: {len(failed)} of {len(ids)} collections')
    if failed:
        logger.warning(f'[Collections] {len(failed)} collections were skipped (errors/not found)')

    ranked = sorted(
        (cid for cid, (_, count, _) in results.items() if count >= config.MIN_COLLECTION_PARTS),
        key=lambda cid: results[cid][2], reverse=True,
    )

    names = {default_language: {cid: results[cid][0] for cid in ranked[:config.MAX_COLLECTIONS] if results[cid][0]}}
    return CollectionsCache(ranked, names, time.time(), config.COLLECTIONS_SORTING_METHOD, config.COLLECTIONS_SORTING_ATTRIBUTE)


async def _fill_names(cache, api, fetcher, languages):
    top = cache.ranking[:config.MAX_COLLECTIONS]
    todo = {lang: [cid for cid in top if cid not in cache.names.get(lang, {})] for lang in languages}
    todo = {lang: ids for lang, ids in todo.items() if ids}
    if not todo:
        return

    total = sum(map(len, todo.values()))
    logger.info(f'[Collections] Fetching names: {len(todo)} languages, {total} requests (~{total / config.TMDB_RPS / 60:.1f} min)...')

    with tqdm(total=total, desc='Collections: names', unit='req', dynamic_ncols=True, mininterval=1.0) as bar:
        for lang, ids in todo.items():
            client = api(lang)

            async def worker(cid):
                res = await fetcher.call(lambda: client.collection(cid).details())
                return res.name

            async def one(cid):
                try:
                    name = await worker(cid)
                    if name:
                        cache.names.setdefault(lang, {})[cid] = name

                except Exception:
                    pass

                finally:
                    bar.update(1)

            bar.set_postfix_str(lang)
            await asyncio.gather(*(one(cid) for cid in ids))
            cache.save()


async def load_or_build_collections_cache(session, api, languages, default_language) -> CollectionsCache:
    cache = CollectionsCache.load()
    fetcher = Fetcher()

    if cache is None or cache.is_stale():
        try:
            fresh = await _build_ranking(session, api, fetcher, default_language)
            fresh.save()
            cache = fresh
        except Exception as e:
            if cache is None:
                raise RuntimeError(f'Unable to build the collections cache: {e}') from e
            logger.warning(f'[Collections] Refresh failed, using the stale cache: {e}')

    await _fill_names(cache, api, fetcher, languages)
    cache.save()
    return cache


# ------------------------- content cache -------------------------

class ContentCache:
    def __init__(self, name: str, max_memory: int = 500):
        self.name = name
        self.dir = os.path.join(config.CACHE_DIR, name)
        self._max_memory = max_memory
        self._memory: dict[str, tuple[float, object]] = {}
        self._inflight: dict[str, asyncio.Future] = {}

    @property
    def ttl(self) -> float:
        return config.CONTENT_TTL_HOURS * 3600

    # --- disk ---

    def _path(self, key: str) -> str:
        digest = hashlib.sha1(key.encode('utf-8')).hexdigest()
        return os.path.join(self.dir, digest[:2], f'{digest}.json')

    def _read(self, key: str):
        path = self._path(key)
        try:
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            if raw.get('version') != CACHE_VERSION or raw.get('key') != key:
                return None
            return float(raw['created']), raw['value']
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning(f'[Cache:{self.name}] Entry {path} is unreadable and will be rebuilt: {e}')
            return None

    def _write(self, key: str, created: float, value) -> None:
        path = self._path(key)
        try:
            save_json(path, {'version': CACHE_VERSION, 'key': key, 'created': created, 'value': value})
        except OSError as e:
            # Сбой записи кеша не должен ломать ответ пользователю.
            logger.warning(f'[Cache:{self.name}] Unable to write {path}: {e}')

    # --- memory ---

    def _remember(self, key: str, entry) -> None:
        if key not in self._memory and len(self._memory) >= self._max_memory:
            self._memory.pop(next(iter(self._memory)))     # выкидываем самую старую
        self._memory[key] = entry

    async def _lookup(self, key: str):
        entry = self._memory.get(key)
        if entry is None:
            entry = await asyncio.to_thread(self._read, key)
            if entry is not None:
                self._remember(key, entry)
        if entry is not None and time.time() - entry[0] < self.ttl:
            return entry[1]
        return None

    # --- api ---

    async def get_or_build(self, key: str, build, cacheable=bool):
        if self.ttl <= 0:
            return await build()

        value = await self._lookup(key)
        if value is not None:
            return value

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._build(key, build, cacheable))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _build(self, key: str, build, cacheable):
        value = await build()
        if cacheable(value):
            created = time.time()
            self._remember(key, (created, value))
            await asyncio.to_thread(self._write, key, created, value)
        return value

    def _purge(self) -> int:
        removed, now = 0, time.time()
        for root, _, files in os.walk(self.dir):
            for filename in files:
                path = os.path.join(root, filename)
                try:
                    if now - os.path.getmtime(path) > self.ttl:
                        os.remove(path)
                        removed += 1
                except OSError:
                    pass
        return removed

    async def purge_expired(self) -> int:
        if self.ttl <= 0:
            return 0
        return await asyncio.to_thread(self._purge)


feeds = ContentCache('feeds', max_memory=500)
metas = ContentCache('meta', max_memory=300)
searches = ContentCache('search', max_memory=300)


async def purge_expired_content() -> None:
    removed = sum(await asyncio.gather(feeds.purge_expired(), metas.purge_expired(), searches.purge_expired()))
    if removed:
        logger.info(f'[Cache] Removed {removed} expired entries')