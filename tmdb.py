import asyncio
from contextlib import asynccontextmanager
from datetime import date

from aiohttp import ClientSession
from themoviedb import aioTMDb

import cache
import config
from cache import ttl_cache
from i18n import i18n
from logger import logger

KINDS = ('movie', 'series')
PAGE_SIZE = 20
CACHE_TTL = 600
ID_TTL = 7 * 24 * 3600

_session: ClientSession | None = None
_clients: dict[str, aioTMDb] = {}


@asynccontextmanager
async def open_session():
	global _session
	async with ClientSession() as session:
		_session = session
		_clients.clear()
		try:
			yield
		finally:
			_session = None
			_clients.clear()


def api(language: str | None = None) -> aioTMDb:
	tag = language if language in i18n.languages else i18n.default_language
	client = _clients.get(tag)
	if client is None:
		client = _clients[tag] = aioTMDb(
			api_key=config.TMDB_API_KEY, language=tag, region=config.TMDB_REGION, session=_session
		)
	return client


# --------------------------- registry ---------------------------

class Registry:
	def __init__(self):
		self._genres: dict[str, dict[str, dict[int, str]]] = {}
		self._collections: dict[str, dict[str, int]] = {}
		self._locks: dict[str, asyncio.Lock] = {}

	# --- genres ---

	async def load_genres(self):
		_cache = await cache.load_or_build_genres_cache(api, i18n.languages)
		movies_count = len(_cache.genres[i18n.default_language]['movie'])
		series_count = len(_cache.genres[i18n.default_language]['series'])

		self._genres.update(_cache.genres)
		logger.info(
			f'[TMDB] Genres: ({movies_count} + {series_count}) x {len(self._genres)} languages')

	async def ensure(self, language: str):
		if language in self._genres:
			return
		lock = self._locks.setdefault(language, asyncio.Lock())
		async with lock:
			if language in self._genres:
				return
			client = api(language)
			movie, tv = await asyncio.gather(client.genres().movie(), client.genres().tv(), return_exceptions=True)
			genres = {}
			for kind, res in (('movie', movie), ('series', tv)):
				if isinstance(res, Exception):
					raise RuntimeError(
						f'Unable to load TMDB genres ({kind}, {language}): {res}') from res
				genres[kind] = {g.id: g.name for g in res.genres or []}
			self._genres[language] = genres
			logger.info(
				f'[TMDB:{language}] Genres: {len(genres["movie"])}/{len(genres["series"])}')

	def genre_names(self, kind: str, language: str, capitalize=False) -> list[str]:
		if capitalize:
			return sorted([i.capitalize() for i in self._genres.get(language, {}).get(kind, {}).values()], key=str.casefold)

		return sorted(self._genres.get(language, {}).get(kind, {}).values(), key=str.casefold)

	def genre_id(self, kind: str, name: str, language: str) -> int | None:
		name = name.casefold()
		genres = self._genres.get(language, {}).get(kind, {})
		return next((i for i, n in genres.items() if n.casefold() == name), None)

	def genre_names_by_ids(self, kind: str, ids, language: str, capitalize=False) -> list[str]:
		genres = self._genres.get(language, {}).get(kind, {})
		if capitalize:
			return [genres[i].capitalize() for i in ids or [] if i in genres]
		return [genres[i] for i in ids or [] if i in genres]

	# --- collections ---

	async def load_collections(self):
		_cache = await cache.load_or_build_collections_cache(_session, api, i18n.languages, i18n.default_language)
		top = _cache.ranking[:config.MAX_COLLECTIONS]
		fallback = _cache.names.get(i18n.default_language, {})

		for lang in i18n.languages:
			names = _cache.names.get(lang, {})
			result = {}
			for cid in top:
				name = names.get(cid) or fallback.get(cid)
				if not name:
					continue
				if name in result:
					name = f'{name} ({cid})'
				result[name] = cid
			self._collections[lang] = result

		logger.info(
			f'[TMDB] Collections: {len(top)} x {len(i18n.languages)} languages')

	def collection_names(self, language: str) -> list[str]:
		return list(self._collections.get(language, {}))

	def collection_id(self, name: str | None, language: str) -> int | None:
		collections = self._collections.get(language, {})
		if not name:
			return next(iter(collections.values()), None)
		return collections.get(name)


registry = Registry()


# --------------------------------- catalogs -----------------------------------

FEEDS = {
	'movie': {
		'popular': dict(sort_by='popularity.desc', vote_count__gte=100),
		'top': dict(sort_by='vote_average.desc', vote_count__gte=500),
		'new': dict(sort_by='primary_release_date.desc', vote_count__gte=20),
	},
	'series': {
		'popular': dict(sort_by='popularity.desc', vote_count__gte=100),
		'top': dict(sort_by='vote_average.desc', vote_count__gte=300),
		'new': dict(sort_by='first_air_date.desc', vote_count__gte=10),
	},
}

RELEASED_BEFORE = {
	'movie': 'primary_release_date__lte',
	'series': 'first_air_date__lte',
}


@ttl_cache(CACHE_TTL)
async def feed(kind: str, which: str, genre_id: int | None, page: int, language: str) -> list:
	params = dict(FEEDS[kind][which])
	if which == 'new':
		params[RELEASED_BEFORE[kind]] = date.today().isoformat()
	if genre_id:
		params['with_genres'] = str(genre_id)
	discover = api(language).discover()
	res = await (discover.movie if kind == 'movie' else discover.tv)(page=page, **params)
	return list(res.results or [])


@ttl_cache(CACHE_TTL)
async def _search_titles(kind: str, text: str, page: int, language: str) -> tuple[list, int]:
	s = api(language).search()
	res = await (s.movies if kind == 'movie' else s.tv)(text, page=page)
	return list(res.results or []), int(getattr(res, 'total_results', 0) or 0)


@ttl_cache(CACHE_TTL)
async def _person_credits(kind: str, text: str, language: str) -> list:
	people = await api(language).search().people(text)
	if not people.results:
		return []
	p = api(language).person(people.results[0].id)
	credits = await (p.movie_credits if kind == 'movie' else p.tv_credits)()

	unique = {}
	for c in [*(credits.cast or []), *(credits.crew or [])]:
		unique.setdefault(c.id, c)
	return sorted(unique.values(), key=lambda c: getattr(c, 'popularity', 0) or 0, reverse=True)


async def search(kind: str, text: str, skip: int, language: str) -> list:
	first, total = await _search_titles(kind, text, 1, language)
	page = skip // PAGE_SIZE + 1

	if skip >= total:
		titles = []
	elif page == 1:
		titles = first
	else:
		titles, _ = await _search_titles(kind, text, page, language)

	need = PAGE_SIZE - len(titles)
	if need <= 0:
		return titles
	credits = await _person_credits(kind, text, language)
	offset = max(0, skip - total)
	return titles + credits[offset:offset + need]


@ttl_cache(CACHE_TTL)
async def collection_parts(collection_id: int, language: str) -> list:
	res = await api(language).collection(collection_id).details()
	return sorted(res.parts or [], key=lambda m: (m.release_date is None, m.release_date or date.max))


@ttl_cache(ID_TTL, maxsize=5000)
async def imdb_id(kind: str, tmdb_id: int) -> str | None:
	try:
		client = api()
		res = await (client.movie(tmdb_id) if kind == 'movie' else client.tv(tmdb_id)).external_ids()
		return res.imdb_id or None
	except Exception as e:
		logger.warning(f'imdb_id({kind}, {tmdb_id}): {e}')
		return None


@ttl_cache(ID_TTL, maxsize=5000)
async def resolve(kind: str, any_id: str) -> int | None:
	if any_id.startswith('tmdb:'):
		return int(any_id[5:]) if any_id[5:].isdigit() else None
	found = await api().find().by_imdb(any_id)
	results = found.movie_results if kind == 'movie' else found.tv_results
	return results[0].id if results else None


@ttl_cache(CACHE_TTL)
async def details(kind: str, tmdb_id: int, language: str):
	client = api(language)
	if kind == 'movie':
		return await client.movie(tmdb_id).details(append_to_response='credits')
	return await client.tv(tmdb_id).details(append_to_response='external_ids,credits')


_season_slots = asyncio.Semaphore(8)


@ttl_cache(CACHE_TTL)
async def season(tv_id: int, number: int, language: str):
	async with _season_slots:
		return await api(language).season(tv_id, number).details()


# ------------------------------- warmup -------------------------------

async def _warmup():
	async with open_session():
		await registry.load_genres()
		await registry.load_collections()


if __name__ == '__main__':
	asyncio.run(_warmup())
