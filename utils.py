import re
from urllib.parse import parse_qs


def iso(d) -> str | None:
    try:
        return d.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    except (AttributeError, TypeError, ValueError):
        return None


def parse_extra(raw_path) -> dict:
    pattern = re.compile(rb'/catalog/[^/]+/[^/]+/(.+)\.json$')
    m = pattern.search(raw_path or b'')
    if not m:
        return {}
    pairs = parse_qs(m.group(1).decode(), keep_blank_values=True)
    return {k: v[0].strip() for k, v in pairs.items()}
