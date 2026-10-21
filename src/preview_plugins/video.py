"""Video (MP4, WebM, MOV, MKV, OGV) in an HTML5 player.

The player streams from /preview-media, which supports HTTP Range requests,
so seeking works and only the parts being watched are downloaded. Whether a
file plays depends on the codecs the browser supports (H.264/AAC in MP4 and
VP8/VP9/AV1 in WebM work almost everywhere)."""
from preview_plugins._util import download_button, esc, media_url, note

NAME = 'video'
MAX_BYTES = 0
EXTENSIONS = ('.mp4', '.m4v', '.webm', '.mov', '.mkv', '.ogv')


def can_preview(filename, content_type):
    return filename.lower().endswith(EXTENSIONS) or bool(content_type and content_type.startswith('video/'))


def render_preview(fileobj, filename, content_type, bucket_name=None):
    url = esc(media_url('preview-media', bucket_name, filename))
    return f'''
    <div class="pv-media">
        <video controls preload="metadata" src="{url}">Your browser does not support the video tag.</video>
        <div class="mt-2 d-flex gap-2">
            {download_button(filename, 'Download video')}
            <a class="btn btn-outline-secondary btn-sm" href="{url}" target="_blank" rel="noopener">
                <i class="fas fa-external-link-alt"></i> Open in new tab
            </a>
        </div>
        {note('If the video does not play, your browser lacks the codec; download it instead.')}
    </div>'''
