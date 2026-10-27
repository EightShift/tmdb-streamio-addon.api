import json
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from logger import logger
import config
import controllers
from controllers import i18n
from tmdb import KINDS
import utils

router = APIRouter(tags=['stremio'])

ICON = Path(__file__).parent / 'icon.png'
RTL = {'ar', 'he', 'fa', 'ur'}


def _base_url(request: Request) -> str:
    return config.PUBLIC_URL or str(request.base_url).rstrip('/')


def _lang(language: str, strict: bool = False) -> str:
    """Язык из URL -> поддерживаемый код. strict: неизвестный язык — 404 (страницы, манифест),
    иначе подставляется язык по умолчанию (каталог/meta не должны падать у уже установленных аддонов)."""
    code = i18n.match(language)
    if code:
        return code
    if strict:
        raise HTTPException(status_code=404, detail='Unknown language')
    return i18n.default_language


def _manifest_url(request: Request, language: str) -> str:
    return f'{_base_url(request)}/{language}/manifest.json'


# ------------------------------ Манифест ----------------------------------

@router.get('/{language}/manifest.json')
@router.get('/manifest.json')
async def manifest(request: Request, language: str = i18n.default_language):
    language = _lang(language, strict=True)
    return {
        # Отдельный id на язык: в Stremio можно поставить несколько языковых версий рядом.
        'id': f'community.local.api.addon.{language}',
        'version': config.VERSION,
        'name': config.NAME,
        'description': i18n.get('description', language),
        'resources': ['catalog', 'meta'],
        'types': list(KINDS),
        'idPrefixes': ['tt', 'tmdb:'],
        'logo': _base_url(request) + '/icon.png',
        'behaviorHints': {'configurable': True, 'configurationRequired': False},
        'catalogs': [c for kind in KINDS for c in await controllers.catalogs(kind, language)],
    }


# ------------------------------- Каталог ----------------------------------

@router.get('/{language}/catalog/{kind}/{cat_id}.json')
@router.get('/{language}/catalog/{kind}/{cat_id}/{extra}.json')
@router.get('/catalog/{kind}/{cat_id}.json')
@router.get('/catalog/{kind}/{cat_id}/{extra}.json')
async def catalog(request: Request, kind: str, cat_id: str, language: str = i18n.default_language):
    language = _lang(language)
    which = cat_id.removeprefix(f'{kind}-')
    if not controllers.is_catalog(kind, which):
        return {'metas': []}

    params = utils.parse_extra(request.scope.get('raw_path', b''))
    try:
        skip = max(0, int(params.get('skip') or 0))
    except ValueError:
        skip = 0

    try:
        metas = await controllers.catalog(
            kind,
            which,
            genre=params.get('genre'),
            skip=skip,
            text=params.get('search', ''),
            language=language,
        )
    except Exception as e:
        logger.warning(f'stremio catalog error: {e}')
        return {'metas': []}
    return {'metas': metas}


# -------------------------------- Meta ------------------------------------

@router.get('/{language}/meta/{kind}/{any_id}.json')
@router.get('/meta/{kind}/{any_id}.json')
async def meta(request: Request, kind: str, any_id: str, language: str = i18n.default_language):
    language = _lang(language)
    try:
        meta = await controllers.meta(kind, any_id, language=language, manifest_url=_manifest_url(request, language))
        return {'meta': meta}
    except Exception as e:
        logger.warning(f'stremio meta error: {e}')
        return {'meta': None}


# --------------------------- Ссылка для установки -------------------------
# Статические пути объявлены раньше '/{language}', чтобы он их не перехватывал.

@router.get('/icon.png')
async def icon():
    if not ICON.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(ICON, media_type='image/png')


@router.get('/{language}/install-link')
@router.get('/install-link')
async def install_link(request: Request, language: str = i18n.default_language):
    url = _manifest_url(request, _lang(language, strict=True))
    return JSONResponse({'manifest': url, 'stremio': 'stremio://' + url.split('://', 1)[1]})


@router.get('/{language}/configure', response_class=HTMLResponse)
@router.get('/{language}/install', response_class=HTMLResponse)
@router.get('/{language}', response_class=HTMLResponse)
@router.get('/install', response_class=HTMLResponse)
@router.get('/', response_class=HTMLResponse)
async def install_page(request: Request, language: str = i18n.default_language):
    language = _lang(language, strict=True)
    base = _base_url(request)
    url = _manifest_url(request, language)
    deep = 'stremio://' + url.split('://', 1)[1]

    language_options = ''.join(
        f'<option value="{escape(code)}"{" selected" if code == language else ""}>'
        f'{escape(i18n.get("language_name", code))}</option>'
        for code in i18n.languages
    )
    base_js = json.dumps(base).replace('<', '\\u003c')
    direction = 'rtl' if language.split('-')[0] in RTL else 'ltr'

    return f'''
    <!doctype html>
    <html lang="{escape(language)}" dir="{direction}">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>{escape(i18n.get("addon_installation", language))}</title>
            <style>
            body {{
                font-family:system-ui,sans-serif;
                background:#14141c;
                color:#eee;
                display:flex;
                justify-content:center;
                padding:48px 16px;
            }}

            .c {{
                max-width:460px;
                width:100%;
                background:#1e1e2a;
                border-radius:14px;
                padding:28px;
            }}

            a.b {{
                display:block;
                text-align:center;
                background:#7b5bf5;
                color:#fff;
                padding:12px;
                border-radius:8px;
                text-decoration:none;
                margin-top:16px;
            }}

            code {{
                display:block;
                direction:ltr;
                text-align:left;
                word-break:break-all;
                background:#14141c;
                padding:10px;
                border-radius:8px;
                margin-top:12px;
                font-size:13px;
            }}

            select {{
                width:100%;
                padding:10px;
                border-radius:8px;
                background:#14141c;
                color:#fff;
                border:1px solid #3b3b4f;
                margin-top:8px;
            }}
            label {{
                font-size:14px;
                color:#9a9ab0;
            }}
            </style>

            <script>
            const BASE = {base_js};
            function changeLanguage(select) {{
                window.location.href = BASE + '/' + select.value + '/install';
            }}
            </script>
        </head>
        <body>
            <div class="c">
                <h2 style="margin-top:0">{escape(config.NAME)}</h2>
                <div style="margin-bottom:16px">
                    <label for="language">{escape(i18n.get("language", language))}:</label>
                    <select id="language" onchange="changeLanguage(this)">
                        {language_options}
                    </select>
                </div>
                <a class="b" href="{escape(deep)}">{escape(i18n.get("install_in_stremio", language))}</a>
                <p style="color:#9a9ab0; font-size:13px">{escape(i18n.get("install_via_link", language))}:</p>
                <code>{escape(url)}</code>
            </div>
        </body>
    </html>
    '''