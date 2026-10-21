"""Helpers shared by preview plugins."""
import html
from urllib.parse import quote, urlencode


def esc(value) -> str:
    """HTML-escape any value (quotes included, so it is safe in attributes)."""
    return html.escape('' if value is None else str(value), quote=True)


def media_url(route: str, bucket_name: str, key: str) -> str:
    """URL of an app route that serves an object inline.

    The key travels as a ``?key=`` query parameter so keys that start with
    '/' survive (a path segment would lose the leading slash).
    """
    return f"/{route}/{quote(bucket_name or '', safe='')}/_?{urlencode({'key': key})}"


def decode_text(data: bytes) -> str:
    """Decode bytes as UTF-8, falling back to Latin-1 (which never fails)."""
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1', errors='replace')


def basename(key: str) -> str:
    return key.rstrip('/').split('/')[-1] or key


def note(text_html: str, kind: str = 'info') -> str:
    """Small notice box under/over a preview. `text_html` must already be safe."""
    return f'<div class="pv-note pv-note-{kind}">{text_html}</div>'


def download_button(key: str, label: str = 'Download') -> str:
    """Button that triggers the UI's normal download for `key`."""
    return (f'<button class="btn btn-outline-primary btn-sm" data-pv-download="{esc(key)}">'
            f'<i class="fas fa-download"></i> {esc(label)}</button>')
