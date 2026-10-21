"""Python source files with line numbers and light keyword highlighting."""
import io
import keyword
import tokenize

from preview_plugins._util import decode_text, esc, note

NAME = 'python'
MAX_BYTES = 512 * 1024


def can_preview(filename, content_type):
    return filename.lower().endswith(('.py', '.pyw', '.pyi')) or content_type in ('text/x-python', 'application/x-python')


def _highlight(src: str) -> str:
    """Token-based highlighting; falls back to plain escaping on errors."""
    out, last = [], (1, 0)
    lines = src.splitlines(keepends=True)

    def text_between(start, end):
        (sr, sc), (er, ec) = start, end
        if sr == er:
            return lines[sr - 1][sc:ec] if sr - 1 < len(lines) else ''
        parts = [lines[sr - 1][sc:]] + lines[sr:er - 1] + [lines[er - 1][:ec] if er - 1 < len(lines) else '']
        return ''.join(parts)

    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            out.append(esc(text_between(last, tok.start)))
            value = esc(tok.string)
            if tok.type == tokenize.NAME and keyword.iskeyword(tok.string):
                value = f'<span class="pv-kw">{value}</span>'
            elif tok.type == tokenize.STRING:
                value = f'<span class="pv-str">{value}</span>'
            elif tok.type == tokenize.COMMENT:
                value = f'<span class="pv-com">{value}</span>'
            out.append(value)
            last = tok.end
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return esc(src)
    return ''.join(out)


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read(MAX_BYTES + 1)
    src = decode_text(data[:MAX_BYTES])
    highlighted = _highlight(src).split('\n')
    width = len(str(len(highlighted)))
    body = '\n'.join(f'<span class="pv-ln">{i:>{width}}</span>{line}' for i, line in enumerate(highlighted, 1))
    extra = note('Showing the first 512 KB only.', 'warn') if len(data) > MAX_BYTES else ''
    return extra + f'<pre class="pv-pre pv-code">{body}</pre>'
