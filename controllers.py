from themoviedb.schemas._enums import SizeType
import asyncio
from urllib.parse import quote

import config
import cache
import tmdb
from i18n import i18n
from tmdb import KINDS, PAGE_SIZE, registry
from utils import iso


LISTS = ('popular', 'top', 'new')
EXTRA_SKIP = {'name': 'skip'}



# -------------------------------- manifest ---------------------------------

async def catalogs(kind, language=i18n.default_language):
    await registry.ensure(language)
    genres = registry.genre_names(kind, language)
    extra = ([{'name': 'genre', 'options': [i[:1].upper() + i[1:] for i in genres]}] if genres else []) + [EXTRA_SKIP]

    result = [
        {'type': kind, 'id': f'{kind}-{key}',
            'name': f'{config.PREFIX} {i18n.get(key, language)}', 'extra': extra}
        for key in LISTS
    ]

    collection_names = registry.collection_names(language)
    if kind == 'movie' and collection_names:
        result.append({
            'type': kind, 'id': 'movie-collections', 'name': f'{config.PREFIX} {i18n.get("collections", language)}',
            'extra': [{'name': 'genre', 'options': collection_names, 'isRequired': True}],
        })
    result.append({
        'type': kind, 'id': f'{kind}-search', 'name': f'{config.PREFIX} {i18n.get("search", language)}',
        'extra': [{'name': 'search', 'isRequired': True}, EXTRA_SKIP],
    })
    return result

# -------------------------------- catalog ----------------------------------


def is_catalog(kind, which):
    if kind not in KINDS:
        return False
    return which in (*LISTS, 'search') or (which == 'collections' and kind == 'movie')


async def catalog(kind, which, genre, skip, text, language=i18n.default_language):
    page = skip // PAGE_SIZE + 1
    await registry.ensure(language)

    if which == 'search':
        if not text:
            return []

        return await cache.searches.get_or_build(
            f'{kind}:{text.casefold()}:{skip}:{language}',
            lambda: _search_previews(kind, text, skip, language),
        )
    elif which == 'collections':
        cid = registry.collection_id(genre, language)
        items = await tmdb.collection_parts(cid, language) if cid and skip == 0 else []
    else:
        gid = registry.genre_id(kind, genre, language) if genre else None
        if genre and not gid:
            return []

        return await cache.feeds.get_or_build(
            f'{kind}:{which}:{gid or 0}:{page}:{language}',
            lambda: _feed_previews(kind, which, gid, page, language),
        )

    return await _previews(kind, items, language)


async def _feed_previews(kind, which, gid, page, language):
    items = await tmdb.feed(kind, which, gid, page, language)
    return await _previews(kind, items, language)


async def _search_previews(kind, text, skip, language):
    items = await tmdb.search(kind, text, skip, language)
    return await _previews(kind, items, language)


async def _previews(kind, items, language):
    return list(await asyncio.gather(*(_preview(kind, m, language=language) for m in items)))


def _stremio_links(kind, manifest_url, genres, cast, directors):
    links = [
        {
            'name': genre,
            'category': 'Genres',
            'url': f'stremio:///discover/{quote(manifest_url, safe="")}/{kind}/{quote(f"{kind}-popular", safe="")}?genre={quote(genre, safe="")}',
        } for genre in genres
    ]

    for category, people in (('Cast', cast), ('Directors', directors)):
        links += [
            {'name': name, 'category': category, 'url': f'stremio:///search?query={quote(name, safe="")}'}
            for name in people
        ]
    return links

# ---------------------------------- meta -----------------------------------


async def meta(kind, any_id, language=i18n.default_language, manifest_url=None):
    if kind not in KINDS:
        return None

    data = await cache.metas.get_or_build(
        f'{kind}:{any_id}:{language}',
        lambda: _meta_record(kind, any_id, language),
    )
    
    if not data:
        return None
    
    if manifest_url:
        data['links'] = _stremio_links(kind, manifest_url, data['genres'], data['cast'], data['director'])

    return data


async def _meta_record(kind, any_id, language):
    tmdb_id = await tmdb.resolve(kind, any_id)
    if not tmdb_id:
        return None

    await registry.ensure(language)
    m = await tmdb.details(kind, tmdb_id, language)

    external_ids = getattr(m, 'external_ids', None)
    imdb = (
        getattr(m, 'imdb_id', None)
        if kind == 'movie'
        else getattr(external_ids, 'imdb_id', None)
    )
    base_id = imdb or f'tmdb:{tmdb_id}'

    genre_names = registry.genre_names_by_ids(kind, [g.id for g in (m.genres or [])], language, capitalize=True)

    data = _build(kind, m, base_id, genre_names)

    credits = getattr(m, 'credits', None)
    cast = getattr(credits, 'cast', None) or []
    crew = getattr(credits, 'crew', None) or []

    actors = cast[:10]
    directors = [cm for cm in crew if (cm.job or '').lower() == 'director']

    data['cast'] = [person.name for person in actors] or None

    complete = True
    if kind == 'movie':
        runtime = getattr(m, 'runtime', None)
        data['runtime'] = f'{runtime} {i18n.get("minute", language)}' if runtime else None
        data['director'] = [person.name for person in directors]
        data['released'] = iso(m.release_date)
    else:
        data['released'] = iso(m.first_air_date)
        data['director'] = [
            person.name for person in getattr(m, 'created_by', [])
        ]
        data['videos'], complete = await _videos(tmdb_id, base_id, m.seasons or [], language)

    return data


async def _videos(tv_id, base_id, seasons, language=i18n.default_language):
    seasons = [s for s in seasons if s.season_number is not None]
    details = await asyncio.gather(
        *(tmdb.season(tv_id, s.season_number, language) for s in seasons), return_exceptions=True
    )
    complete = not any(isinstance(d, Exception) for d in details)

    videos = []
    for s, d in zip(seasons, details):
        sn = s.season_number
        episodes = [] if isinstance(d, Exception) else d.episodes or []
        if episodes:
            for e in episodes:
                if e.episode_number is None:
                    continue
                videos.append({
                    'id': f'{base_id}:{sn}:{e.episode_number}',
                    'title': e.name or f'{i18n.get("episode", language)} {e.episode_number}',
                    'season': sn,
                    'episode': e.episode_number,
                    'released': iso(e.air_date) or '1970-01-01T00:00:00.000Z',
                    'overview': e.overview or None,
                    'thumbnail': e.still_url(SizeType.w500),
                })
        else:
            for en in range(1, (s.episode_count or 0) + 1):
                videos.append({
                    'id': f'{base_id}:{sn}:{en}',
                    'title': f'{i18n.get("episode", language)} {en}',
                    'season': sn,
                    'episode': en,
                    'released': iso(s.air_date) or '1970-01-01T00:00:00.000Z',
                })
    return videos, complete


# ------------------------------- preview -------------------------------

async def _preview(kind, m, language=i18n.default_language):
    imdb = await tmdb.imdb_id(kind, m.id)
    genres = registry.genre_names_by_ids(kind, m.genre_ids, language, capitalize=True)
    return {k: v for k, v in _build(kind, m, imdb or f'tmdb:{m.id}', genres).items() if v is not None}


def _build(kind, m, stremio_id, genre_names):
    is_movie = kind == 'movie'
    name = (
        m.title or m.original_title
        if is_movie
        else m.name or m.original_name
    )
    date = m.release_date if is_movie else m.first_air_date

    return {
        'id': stremio_id,
        'type': kind,
        'name': name,
        'poster': m.poster_url(SizeType.w500),
        'posterShape': 'poster',
        'background': m.backdrop_url(SizeType.original),
        'description': m.overview or None,
        'releaseInfo': str(date.year) if date else None,
        'released': iso(date),
        'imdbRating': (
            f'{m.vote_average:.1f}'
            if m.vote_average is not None
            else None
        ),
        'genres': genre_names,
        'language': m.original_language or None,
        'country': [
            c.iso_3166_1
            for c in (getattr(m, 'production_countries', None) or [])
        ] or None
    }