# Preview plugins

Everything S3FastBrowser shows in the **Preview** dialog is produced by a *preview
plugin*: a small Python module that says which files it understands and returns an
HTML fragment for them. The built-in plugins live in
[`src/preview_plugins/`](../src/preview_plugins/); you can add your own without
rebuilding the image.

- [How a preview is chosen](#how-a-preview-is-chosen)
- [Built-in plugins](#built-in-plugins)
- [Configuration](#configuration)
- [Writing a plugin](#writing-a-plugin)

## How a preview is chosen

When you click the eye icon, the browser calls `GET /preview/<bucket>/_?key=<key>`. The
server then:

1. reads the object's size and `Content-Type` with a `HEAD` request;
2. walks the plugins in order of `PRIORITY` (lowest first; ties sorted by name);
3. for the first plugin whose `can_preview(key, content_type)` returns `True`, fetches
   as much of the object as the plugin asks for (`MAX_BYTES`) and calls
   `render_preview(...)`;
4. if the plugin returns HTML, sends it to the browser together with the plugin name
   (shown under the preview as *Preview plugin: name*). If it returns `None` or
   raises, the next matching plugin is tried.

The `fallback` plugin accepts everything and runs last, so there is always an answer.

Plugins that only need a URL (PDF, video, audio) read nothing: the HTML they return
points the browser at `/preview-pdf/...` or `/preview-media/...`, which stream the
object from S3 with HTTP Range support.

Plugins are loaded once at start-up; the log lists them:

```
Preview plugins: audio(50), csv(50), html(50), image(50), json(50), pdf(50), python(50), rtf(50), shell(50), video(50), xlsx(50), text(90), fallback(100)
```

## Built-in plugins

| Plugin | File | Handles | Reads | Notes |
|---|---|---|---|---|
| `image` | `image.py` | `.jpg .jpeg .png .gif .webp .bmp .svg .avif .ico`, `image/*` | whole object | Inline `<img>` from a `data:` URI on a checkerboard background. SVG is safe this way: images never run scripts. |
| `pdf` | `pdf.py` | `.pdf`, `application/pdf` | nothing | The browser's own PDF viewer in an iframe, plus *Download* and *Open in new tab*. |
| `video` | `video.py` | `.mp4 .m4v .webm .mov .mkv .ogv`, `video/*` | nothing | HTML5 player streaming from `/preview-media` (seeking works). Playback depends on the browser's codecs. |
| `audio` | `audio.py` | `.mp3 .wav .ogg .oga .opus .flac .aac .m4a .weba`, `audio/*` | nothing | HTML5 audio player streaming from `/preview-media`. |
| `csv` | `csv.py` | `.csv .tsv`, `*csv*` types | first 100 KB | Table with the first 200 rows; delimiter detected automatically (`, ; tab \|`). |
| `xlsx` | `xls.py` | `.xlsx .xlsm` | whole object | One tab per sheet (up to 10), first 200 rows and 50 columns each, computed values. Legacy binary `.xls` is not supported by openpyxl and falls through. |
| `json` | `json.py` | `.json .geojson .jsonl .ndjson`, `*json*` types | up to 5 MB | Pretty-printed; JSON Lines one record per line; invalid JSON is shown as text with the parse error. Larger files fall through to `text`. |
| `html` | `html.py` | `.html .htm`, `text/html` | first 512 KB | Rendered in an iframe with an empty `sandbox` attribute (no scripts, forms, plugins or same-origin access), plus the source in a collapsible block. |
| `python` | `python.py` | `.py .pyw .pyi`, Python types | first 512 KB | Line numbers and token-based highlighting of keywords, strings and comments. |
| `shell` | `shell.py` | `.sh .bash .zsh .fish .csh .tcsh .ksh .dash` | first 100 KB | Line numbers, comment and keyword highlighting. |
| `rtf` | `rtf.py` | `.rtf`, RTF types | first 2 MB | Converted to plain text (formatting is dropped). |
| `text` | `text.py` | many text extensions (`.txt .log .md .xml .yml .toml .ini .conf .sql .js .ts .go .rs .java .c ...`), `text/*` | first 100 KB | Line numbers; prose formats wrap long lines. Priority 90, so specialised plugins win. |
| `fallback` | `fallback.py` | everything | first 100 KB | If the bytes look like text (no NUL bytes, >90 % printable) they are shown as text; otherwise a hex dump of the first 4 KB. Priority 100. |

Screenshots of each one are in [PREVIEWS.md](PREVIEWS.md).

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `S3FB_PREVIEW_MAX_MB` | `25` | Plugins that read the whole object (`MAX_BYTES = None`: image, xlsx) are skipped for larger objects; the next matching plugin (usually `fallback`) is used instead. |
| `S3FB_PLUGIN_DIRS` | *(empty)* | Extra directories to load plugins from, separated by `:`. |

To change a built-in plugin, copy it to your plugin directory under a **different
module name** and give it a lower `PRIORITY` so it runs first.

## Writing a plugin

A plugin is one Python file. Minimal example, `markdown_render.py`, which renders
Markdown as HTML (using the `markdown` package) instead of showing the source:

```python
"""Render Markdown files as formatted HTML."""
import markdown                       # pip install markdown (in your image)

from preview_plugins._util import decode_text, esc

NAME = 'markdown'
PRIORITY = 40                         # before 'text' (90)
MAX_BYTES = 512 * 1024                # read at most the first 512 KB


def can_preview(filename, content_type):
    return filename.lower().endswith(('.md', '.markdown')) or content_type == 'text/markdown'


def render_preview(fileobj, filename, content_type, bucket_name=None):
    source = decode_text(fileobj.read(MAX_BYTES))
    # escape=True-like behaviour: drop raw HTML from the document before rendering
    body = markdown.markdown(esc(source), extensions=['tables', 'fenced_code'])
    return f'<div class="pv-doc" style="font-family: sans-serif">{body}</div>'
```

### The contract

| Name | Required | Meaning |
|---|---|---|
| `can_preview(filename, content_type) -> bool` | yes | Decide from the key and the stored `Content-Type` (which may be empty or `application/octet-stream`). Must be cheap: no I/O. |
| `render_preview(fileobj, filename, content_type, bucket_name=None) -> str \| None` | yes | Return an HTML fragment, or `None` to let the next plugin try. Exceptions are logged and the next plugin is tried. |
| `NAME` | no | Shown under the preview and in the log. Defaults to the module name. |
| `PRIORITY` | no | Lower runs first. Default `50`. Built-ins use 50, `text` 90, `fallback` 100. |
| `MAX_BYTES` | no | `None` (default): `fileobj` contains the whole object. `0`: nothing is downloaded and `fileobj` is `None`. `N`: only the first `N` bytes (plus one, so you can detect truncation) are fetched with an HTTP Range request. |

`filename` is the full object key (it may contain `/` and may start with `/`).

### Helpers

`preview_plugins._util` has small helpers you can import:

| Helper | Use |
|---|---|
| `esc(value)` | HTML-escape anything (quotes included). **Escape everything that comes from the object or its key.** |
| `decode_text(data)` | Bytes to text: UTF-8, falling back to Latin-1. |
| `media_url(route, bucket_name, key)` | URL of `/preview-media` or `/preview-pdf` for this object (key passed as a query parameter). |
| `note(html, kind='info'|'warn')` | Small notice box. `html` must already be safe. |
| `download_button(key, label)` | A button that triggers the normal download for `key`. |

### Styling

The fragment is inserted into the preview dialog, which uses Bootstrap 5 and Font
Awesome 6 (`fas fa-...`). These CSS classes follow the light/dark theme:

| Class | For |
|---|---|
| `pv-pre`, `pv-code` | scrollable monospace block; add `pv-wrap` to wrap long lines |
| `pv-ln` | line-number gutter span inside `pv-pre` |
| `pv-kw`, `pv-str`, `pv-com` | keyword, string and comment colours |
| `pv-table-wrap`, `pv-table` | scrollable table with sticky header |
| `pv-doc` | white "paper" block for documents |
| `pv-image`, `pv-media`, `pv-pdf`, `pv-html` | containers used by the built-in plugins |
| `pv-note`, `pv-note-warn` | notice boxes (use `note()`) |

### Security rules

Preview HTML is inserted with `innerHTML` into the app's own page, which holds the
user's session. Treat object content and keys as hostile:

- Escape every value that comes from the object, its key or its metadata (`esc()`).
- Never put object content into `<script>`, event handler attributes or `style`.
- To show untrusted HTML, use an iframe with an empty `sandbox` attribute and a `data:`
  URI, as the `html` plugin does.
- The page's Content-Security-Policy blocks external resources (`default-src 'self'`,
  images also from `data:`/`blob:`). Plugins cannot load scripts or styles from CDNs;
  bundle what you need under `static/` instead.

### Installing a plugin

With Docker, mount a directory and point `S3FB_PLUGIN_DIRS` at it:

```yaml
# docker-compose.override.yml
services:
  s3fastbrowser:
    volumes:
      - ./plugins:/plugins:ro
    environment:
      S3FB_PLUGIN_DIRS: /plugins
```

Restart the container; the start-up log lists the plugins it loaded and any that
failed to import. If your plugin needs extra Python packages, build a small image on
top of the official one:

```dockerfile
FROM s3fastbrowser:latest
USER root
RUN pip install --no-cache-dir markdown
USER s3fb
```

### Testing a plugin

Plugins are plain functions, so they are easy to test without S3:

```python
import io
from preview_plugins import get_plugins

def test_markdown_plugin():
    plugin = next(p for p in get_plugins() if p.NAME == 'markdown')
    html = plugin.render_preview(io.BytesIO(b'# Hi <script>'), 'a.md', 'text/markdown')
    assert '<h1>' in html and '<script>' not in html
```

Run the test suite with `pytest` (see [CONTRIBUTING.md](../CONTRIBUTING.md)).
