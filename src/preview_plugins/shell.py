"""Shell scripts (sh, bash, zsh, fish, ksh, ...) with line numbers and
comment / keyword highlighting."""
import re

from preview_plugins._util import decode_text, esc, note

NAME = 'shell'
MAX_BYTES = 100 * 1024
EXTENSIONS = ('.sh', '.bash', '.zsh', '.fish', '.csh', '.tcsh', '.ksh', '.dash')
KEYWORDS = r'\b(if|then|else|elif|fi|for|while|until|do|done|case|esac|function|return|exit|in|local|export)\b'


def can_preview(filename, content_type):
    return filename.lower().endswith(EXTENSIONS) or bool(content_type and 'shellscript' in content_type)


def _line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith('#!'):
        return f'<span class="pv-kw">{esc(line)}</span>'
    if stripped.startswith('#'):
        return f'<span class="pv-com">{esc(line)}</span>'
    return re.sub(KEYWORDS, r'<span class="pv-kw">\1</span>', esc(line))


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read(MAX_BYTES + 1)
    lines = decode_text(data[:MAX_BYTES]).split('\n')
    width = len(str(len(lines)))
    body = '\n'.join(f'<span class="pv-ln">{i:>{width}}</span>{_line(l)}' for i, l in enumerate(lines, 1))
    extra = note('Showing the first 100 KB only.', 'warn') if len(data) > MAX_BYTES else ''
    return extra + f'<pre class="pv-pre pv-code">{body}</pre>'
