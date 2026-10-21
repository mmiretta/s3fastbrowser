"""Plain text: logs, Markdown, XML, YAML, INI, source code and anything with a
text/* content type. Shows the first 100 KB with line numbers."""
from preview_plugins._util import decode_text, esc, note

NAME = 'text'
PRIORITY = 90          # after the specialised plugins
MAX_BYTES = 100 * 1024

TEXT_EXTENSIONS = (
    '.txt', '.log', '.md', '.markdown', '.rst', '.xml', '.yml', '.yaml', '.toml',
    '.ini', '.cfg', '.conf', '.config', '.env', '.properties', '.tsv',
    '.js', '.mjs', '.ts', '.css', '.scss', '.sql', '.go', '.rs', '.java', '.kt',
    '.c', '.h', '.cpp', '.hpp', '.cs', '.rb', '.php', '.pl', '.r', '.lua',
    '.ps1', '.bat', '.cmd', '.dockerfile', '.gitignore', '.tf', '.hcl',
)


def is_ascii_text(content: bytes, sample_size: int = 1024) -> bool:
    """Heuristic: does this sample look like human-readable text?"""
    if not content:
        return False
    sample = content[:sample_size]
    if b'\x00' in sample:
        return False
    text_sample = sample.decode('utf-8', errors='ignore')
    if not text_sample.strip():
        return False
    printable = sum(1 for c in text_sample if c.isprintable() or c.isspace())
    if printable / len(text_sample) <= 0.9:
        return False
    lines = text_sample.split('\n')
    if len(lines) > 1:
        return sum(len(line) for line in lines) / len(lines) < 200
    return len(text_sample.strip()) < 1000


def can_preview(filename, content_type):
    if filename.lower().endswith(TEXT_EXTENSIONS):
        return True
    return bool(content_type and content_type.startswith('text/'))


def render_text(content_bytes: bytes, filename: str, truncated: bool, detected: bool = False) -> str:
    text = decode_text(content_bytes)
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    width = len(str(len(lines)))
    body = '\n'.join(f'<span class="pv-ln">{i:>{width}}</span>{esc(line)}'
                     for i, line in enumerate(lines, 1))
    notes = []
    if detected:
        notes.append(note('<i class="fas fa-info-circle"></i> No plugin for this file type; '
                          'the content looks like text, so it is shown as plain text.'))
    if truncated:
        notes.append(note('<i class="fas fa-cut"></i> Showing the first 100 KB only.', 'warn'))
    prose = detected or filename.lower().endswith(('.txt', '.md', '.markdown', '.rst', '.log'))
    css = 'pv-pre pv-code pv-wrap' if prose else 'pv-pre pv-code'
    return ''.join(notes) + f'<pre class="{css}">{body}</pre>'


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read(MAX_BYTES + 1)
    return render_text(data[:MAX_BYTES], filename, truncated=len(data) > MAX_BYTES)
