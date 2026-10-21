"""Audio (MP3, WAV, OGG/Opus, FLAC, AAC/M4A) in an HTML5 player, streamed with
HTTP Range support from /preview-media."""
from preview_plugins._util import download_button, esc, media_url

NAME = 'audio'
MAX_BYTES = 0
EXTENSIONS = ('.mp3', '.wav', '.ogg', '.oga', '.opus', '.flac', '.aac', '.m4a', '.weba')


def can_preview(filename, content_type):
    return filename.lower().endswith(EXTENSIONS) or bool(content_type and content_type.startswith('audio/'))


def render_preview(fileobj, filename, content_type, bucket_name=None):
    url = esc(media_url('preview-media', bucket_name, filename))
    return f'''
    <div class="pv-media pv-audio">
        <div class="pv-audio-title"><i class="fas fa-music"></i> {esc(filename.split('/')[-1])}</div>
        <audio controls preload="metadata" src="{url}">Your browser does not support the audio tag.</audio>
        <div class="mt-2">{download_button(filename, 'Download audio')}</div>
    </div>'''
