"""CSV / TSV files as a table (first 100 KB, up to 200 rows)."""
import csv as _csv
import io

from preview_plugins._util import decode_text, esc, note

NAME = 'csv'
MAX_BYTES = 100 * 1024
MAX_ROWS = 200


def can_preview(filename, content_type):
    name = filename.lower()
    return name.endswith(('.csv', '.tsv')) or bool(content_type and 'csv' in content_type)


def render_preview(fileobj, filename, content_type, bucket_name=None):
    data = fileobj.read(MAX_BYTES + 1)
    truncated = len(data) > MAX_BYTES
    text = decode_text(data[:MAX_BYTES])
    if truncated:  # drop the last, probably partial, line
        text = text.rsplit('\n', 1)[0]
    if filename.lower().endswith('.tsv'):
        dialect = 'excel-tab'
    else:
        try:
            dialect = _csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        except _csv.Error:
            dialect = 'excel'
    rows = list(_csv.reader(io.StringIO(text), dialect))
    shown = rows[:MAX_ROWS]
    if not shown:
        return note('Empty CSV file.')
    out = ['<div class="pv-table-wrap"><table class="table table-sm table-bordered pv-table"><thead><tr>']
    out += [f'<th>{esc(c)}</th>' for c in shown[0]]
    out.append('</tr></thead><tbody>')
    for row in shown[1:]:
        out.append('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in row) + '</tr>')
    out.append('</tbody></table></div>')
    if truncated or len(rows) > MAX_ROWS:
        out.append(note(f'Showing the first {len(shown)} rows.', 'warn'))
    return ''.join(out)
