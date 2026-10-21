"""HTML pages, rendered in a fully sandboxed iframe (no scripts, no forms,
no same-origin access), plus a tab with the source."""
import base64

from preview_plugins._util import decode_text, esc

NAME = 'html'
MAX_BYTES = 512 * 1024


def can_preview(filename, content_type):
    return filename.lower().endswith(('.html', '.htm')) or content_type == 'text/html'


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read(MAX_BYTES)
    b64 = base64.b64encode(data).decode('ascii')
    source = esc(decode_text(data))
    return f'''
    <div class="pv-html">
        <iframe sandbox src="data:text/html;base64,{b64}" title="HTML preview"></iframe>
        <details class="mt-2"><summary>Source</summary><pre class="pv-pre pv-code">{source}</pre></details>
    </div>'''
