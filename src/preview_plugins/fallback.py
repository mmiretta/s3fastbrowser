"""Last resort for unknown types. If the first bytes look like text they are
shown as text; otherwise a hex dump of the first 4 KB is shown."""
from preview_plugins import text
from preview_plugins._util import esc, note

NAME = 'fallback'
PRIORITY = 100         # always last
MAX_BYTES = 100 * 1024


def can_preview(filename, content_type):
    return True


def _hexdump(data: bytes) -> str:
    rows = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        rows.append(f'{offset:08x}  {hex_part:<47}  {ascii_part}')
    return '\n'.join(rows)


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read(MAX_BYTES + 1)
    if not data:
        return note('<i class="fas fa-info-circle"></i> <strong>Empty object</strong> - it contains no data.')
    if text.is_ascii_text(data):
        return text.render_text(data[:MAX_BYTES], filename, truncated=len(data) > MAX_BYTES, detected=True)
    sample = data[:4096]
    return (note(f'<i class="fas fa-exclamation-triangle"></i> <strong>No preview for this file type</strong> '
                 f'({esc(content_type or "unknown type")}). Hex dump of the first {len(sample)} bytes; '
                 f'download the object to open it with the right application.', 'warn')
            + f'<pre class="pv-pre pv-hex">{esc(_hexdump(sample))}</pre>')
