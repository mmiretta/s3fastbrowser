"""RTF documents, converted to plain text (formatting is not preserved)."""
import re

from preview_plugins._util import decode_text, esc, note

NAME = 'rtf'
MAX_BYTES = 2 * 1024 * 1024


def can_preview(filename, content_type):
    return filename.lower().endswith('.rtf') or content_type in ('application/rtf', 'text/rtf')


def strip_rtf(rtf: str) -> str:
    """Very small RTF-to-text converter: good enough for a preview."""
    text = re.sub(r'\\par[d]?\b ?', '\n', rtf)
    text = re.sub(r'\\line\b ?', '\n', text)
    text = re.sub(r'\\tab\b ?', '\t', text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode('cp1252', 'replace'), text)
    # Drop groups that hold metadata rather than text.
    text = re.sub(r'\{\\\*[^{}]*\}', '', text)
    text = re.sub(r'\{\\(fonttbl|colortbl|stylesheet|info)[^{}]*(\{[^{}]*\}[^{}]*)*\}', '', text)
    text = re.sub(r'\\[a-zA-Z]+-?\d* ?', '', text)   # control words
    text = re.sub(r'\\[^a-zA-Z]', '', text)           # control symbols
    text = text.replace('{', '').replace('}', '')
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def render_preview(fileobj, filename, content_type, bucket_name=None):
    plain = strip_rtf(decode_text(fileobj.read(MAX_BYTES)))
    return (f'<div class="pv-doc">{esc(plain)}</div>'
            + note('<i class="fas fa-info-circle"></i> Basic RTF preview: formatting is not preserved.'))
