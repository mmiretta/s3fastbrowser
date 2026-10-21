"""PDF documents, rendered by the browser's built-in PDF viewer.

No content is read by the plugin: the iframe loads /preview-pdf, which streams
the object from S3."""
from preview_plugins._util import download_button, esc, media_url

NAME = 'pdf'
MAX_BYTES = 0


def can_preview(filename, content_type):
    return filename.lower().endswith('.pdf') or content_type == 'application/pdf'


def render_preview(fileobj, filename, content_type, bucket_name=None):
    url = esc(media_url('preview-pdf', bucket_name, filename))
    return f'''
    <div class="pv-pdf">
        <div class="d-flex gap-2 mb-2">
            {download_button(filename, 'Download PDF')}
            <a class="btn btn-outline-secondary btn-sm" href="{url}" target="_blank" rel="noopener">
                <i class="fas fa-external-link-alt"></i> Open in new tab
            </a>
        </div>
        <iframe src="{url}" title="PDF preview"></iframe>
    </div>'''
