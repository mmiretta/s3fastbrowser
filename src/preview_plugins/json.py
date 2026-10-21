"""JSON documents, pretty-printed (up to 5 MB). Invalid JSON is shown as text
with the parse error."""
import json as _json

from preview_plugins._util import decode_text, esc, note

NAME = 'json'
MAX_BYTES = 5 * 1024 * 1024


def can_preview(filename, content_type):
    name = filename.lower()
    return name.endswith(('.json', '.geojson', '.jsonl', '.ndjson')) or bool(content_type and 'json' in content_type)


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return None  # too big to pretty-print; let the text plugin show the start
    text = decode_text(data)
    if filename.lower().endswith(('.jsonl', '.ndjson')):
        try:
            pretty = '\n'.join(_json.dumps(_json.loads(line), ensure_ascii=False)
                               for line in text.splitlines() if line.strip())
            return f'<pre class="pv-pre pv-code">{esc(pretty)}</pre>'
        except ValueError:
            pass
    try:
        pretty = _json.dumps(_json.loads(text), indent=2, ensure_ascii=False)
    except ValueError as e:
        return (note(f'<i class="fas fa-exclamation-triangle"></i> Invalid JSON: {esc(e)}', 'warn')
                + f'<pre class="pv-pre pv-code">{esc(text[:100 * 1024])}</pre>')
    return f'<pre class="pv-pre pv-code pv-json">{esc(pretty)}</pre>'
