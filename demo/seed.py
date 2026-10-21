"""Fill an S3 endpoint with synthetic demo data for S3FastBrowser.

Every file is generated on the fly: test-pattern images and videos and sine
tones from ffmpeg, a hand-written PDF, spreadsheets built with openpyxl and
placeholder text. Nothing here is real data.

Environment:
    S3_ENDPOINT        default http://minio:9000
    S3_ACCESS_KEY      required
    S3_SECRET_KEY      required
    SEED_FORCE=1       re-create objects even if the buckets already exist
"""
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from openpyxl import Workbook

ENDPOINT = os.environ.get('S3_ENDPOINT', 'http://minio:9000')
ACCESS_KEY = os.environ.get('S3_ACCESS_KEY')
SECRET_KEY = os.environ.get('S3_SECRET_KEY')
FORCE = os.environ.get('SEED_FORCE') == '1'
MARKER_BUCKET = 'demo-versioned'  # created last: if it exists, seeding finished

random.seed(42)

LOREM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor "
         "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
         "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.")


def s3_client():
    return boto3.client('s3', endpoint_url=ENDPOINT, aws_access_key_id=ACCESS_KEY,
                        aws_secret_access_key=SECRET_KEY, region_name='us-east-1',
                        config=Config(s3={'addressing_style': 'path'}, retries={'max_attempts': 10}))


def wait_for(s3):
    for _ in range(60):
        try:
            s3.list_buckets()
            return
        except Exception as e:  # endpoint still starting
            print(f'waiting for {ENDPOINT}: {e}', flush=True)
            time.sleep(2)
    sys.exit('S3 endpoint never became ready')


def ffmpeg(*args) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'out' + args[-1])
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args[:-1], out]
        subprocess.run(cmd, check=True)
        with open(out, 'rb') as fh:
            return fh.read()


# ---------------------------------------------------------------- generators
def image(source: str, ext: str, size='960x540') -> bytes:
    return ffmpeg('-f', 'lavfi', '-i', f'{source}=size={size}', '-frames:v', '1', ext)


def video_mp4() -> bytes:
    return ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=6',
                  '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
                  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-crf', '30',
                  '-c:a', 'aac', '-b:a', '64k', '-shortest', '-movflags', '+faststart', '.mp4')


def video_webm() -> bytes:
    return ffmpeg('-f', 'lavfi', '-i', 'mandelbrot=size=640x360:rate=25', '-t', '5',
                  '-c:v', 'libvpx-vp9', '-b:v', '400k', '-deadline', 'realtime', '-an', '.webm')


def audio(ext: str, freq: int, codec_args=()) -> bytes:
    return ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={freq}:duration=8',
                  '-af', 'volume=0.3,afade=t=in:d=0.5,afade=t=out:st=7:d=1', *codec_args, ext)


def pdf(title: str, lines) -> bytes:
    """A minimal, valid one-page PDF (Helvetica, no external libraries)."""
    def pdf_str(t):
        return t.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    text = ['BT', '/F1 22 Tf', '72 740 Td', f'({pdf_str(title)}) Tj', '/F1 11 Tf', '0 -34 Td']
    for line in lines:
        text += [f'({pdf_str(line)}) Tj', '0 -16 Td']
    text.append('ET')
    stream = '\n'.join(text).encode('latin-1')
    graphics = b'0.96 0.65 0.14 rg 72 760 468 4 re f\n0.12 0.14 0.2 rg\n'
    content = graphics + stream
    objs = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    out = io.BytesIO()
    out.write(b'%PDF-1.4\n')
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = out.tell()
    out.write(f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode())
    for off in offsets:
        out.write(f'{off:010d} 00000 n \n'.encode())
    out.write(f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return out.getvalue()


def xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = 'Inventory'
    ws.append(['SKU', 'Product', 'Category', 'Stock', 'Unit price', 'Updated'])
    products = ['Widget', 'Gadget', 'Sprocket', 'Gizmo', 'Doohickey', 'Thingamajig']
    for i in range(1, 41):
        ws.append([f'SKU-{1000 + i}', f'{random.choice(products)} {chr(64 + (i % 26) + 1)}',
                   random.choice(['Hardware', 'Tools', 'Spare parts']), random.randint(0, 500),
                   round(random.uniform(2, 250), 2), str(date(2026, 1, 1) + timedelta(days=i * 3))])
    ws2 = wb.create_sheet('Summary')
    ws2.append(['Category', 'Items'])
    for cat in ('Hardware', 'Tools', 'Spare parts'):
        ws2.append([cat, random.randint(5, 20)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_data() -> bytes:
    rows = ['date,region,orders,revenue']
    for i in range(60):
        d = date(2026, 1, 1) + timedelta(days=i)
        for region in ('north', 'south', 'east'):
            rows.append(f'{d},{region},{random.randint(10, 90)},{random.randint(1000, 9000)}.{random.randint(0, 99):02d}')
    return ('\n'.join(rows) + '\n').encode()


def log_data() -> bytes:
    levels = ['INFO'] * 8 + ['WARN', 'ERROR']
    msgs = ['request served', 'cache miss', 'user session started', 'job queued', 'job finished',
            'slow response from upstream', 'retrying connection', 'configuration reloaded']
    out = []
    t = 1767225600  # 2026-01-01
    for _ in range(400):
        t += random.randint(1, 30)
        lvl = random.choice(levels)
        out.append(f'{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))} {lvl:<5} worker-{random.randint(1, 4)} '
                   f'{random.choice(msgs)} id={random.randint(10000, 99999)}')
    return ('\n'.join(out) + '\n').encode()


PY_SAMPLE = b'''"""Example script stored in the demo bucket."""
import json
from dataclasses import dataclass


@dataclass
class Item:
    name: str
    quantity: int = 0

    def total(self, price: float) -> float:
        # Multiply quantity by unit price
        return self.quantity * price


def load(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        return [Item(**row) for row in json.load(fh)]


if __name__ == "__main__":
    for item in load("items.json"):
        print(f"{item.name}: {item.total(9.99):.2f}")
'''

SH_SAMPLE = b'''#!/usr/bin/env bash
# Rotate application logs older than 7 days (example).
set -euo pipefail

LOG_DIR="${1:-/var/log/example}"
for file in "$LOG_DIR"/*.log; do
    if [ -f "$file" ] && [ "$(find "$file" -mtime +7)" ]; then
        gzip "$file"
        echo "compressed $file"
    fi
done
exit 0
'''

MD_SAMPLE = f'''# Project notes

This bucket holds **sample data** generated for the S3FastBrowser demo.

## Checklist

- [x] Upload images and videos
- [x] Add a few spreadsheets
- [ ] Review the lifecycle rules

{LOREM}
'''.encode()

HTML_SAMPLE = b'''<!doctype html>
<html><head><meta charset="utf-8"><title>Demo page</title>
<style>body{font-family:sans-serif;margin:2rem;color:#223}h1{color:#d98a00}
.card{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:.5rem 0}</style></head>
<body><h1>Static demo page</h1>
<p>This HTML file is rendered inside a sandboxed iframe: scripts are disabled.</p>
<div class="card"><strong>Card one</strong><p>Lorem ipsum dolor sit amet.</p></div>
<div class="card"><strong>Card two</strong><p>Consectetur adipiscing elit.</p></div>
<script>document.body.innerHTML = "scripts should not run";</script>
</body></html>
'''

RTF_SAMPLE = (r'{\rtf1\ansi\deff0{\fonttbl{\f0 Times New Roman;}}\f0\fs24 '
              r'{\b Meeting summary}\par\par '
              r'Attendees: Team A, Team B.\par '
              r'Decision: move the archive to the new storage tier next quarter.\par\par '
              + LOREM + r'\par}').encode()

JSON_SAMPLE = json.dumps({
    'name': 'demo-config',
    'version': 3,
    'features': {'thumbnails': True, 'retention_days': 30, 'regions': ['north', 'south', 'east']},
    'endpoints': [{'id': i, 'url': f'https://service-{i}.example.com', 'enabled': i % 2 == 0} for i in range(1, 5)],
}).encode()

YAML_SAMPLE = b'''# Example pipeline definition
pipeline:
  name: nightly-export
  schedule: "0 2 * * *"
  steps:
    - name: extract
      image: example/extract:1.4
    - name: compress
      args: ["--level", "9"]
    - name: upload
      target: s3://demo-backups/exports/
'''


# ------------------------------------------------------------------ helpers
def ensure_bucket(s3, name, versioning=False):
    try:
        s3.create_bucket(Bucket=name)
        print(f'created bucket {name}', flush=True)
    except ClientError as e:
        if e.response['Error']['Code'] not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            raise
    if versioning:
        s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={'Status': 'Enabled'})


def put(s3, bucket, key, body, content_type=None, metadata=None, tags=None):
    extra = {}
    if content_type:
        extra['ContentType'] = content_type
    if metadata:
        extra['Metadata'] = metadata
    if tags:
        extra['Tagging'] = '&'.join(f'{k}={v}' for k, v in tags.items())
    s3.put_object(Bucket=bucket, Key=key, Body=body, **extra)
    print(f'  s3://{bucket}/{key} ({len(body)} bytes)', flush=True)


def main():
    if not ACCESS_KEY or not SECRET_KEY:
        sys.exit('S3_ACCESS_KEY and S3_SECRET_KEY are required')
    s3 = s3_client()
    wait_for(s3)

    existing = {b['Name'] for b in s3.list_buckets().get('Buckets', [])}
    if MARKER_BUCKET in existing and not FORCE:
        print('demo data already present (set SEED_FORCE=1 to regenerate)')
        return

    # --- media
    ensure_bucket(s3, 'demo-media')
    put(s3, 'demo-media', 'images/test-pattern.png', image('testsrc2', '.png'), 'image/png',
        metadata={'camera': 'synthetic', 'author': 'demo'}, tags={'collection': 'patterns'})
    put(s3, 'demo-media', 'images/colour-bars.jpg', image('smptehdbars', '.jpg', '1280x720'), 'image/jpeg')
    put(s3, 'demo-media', 'images/mandelbrot.webp', image('mandelbrot', '.webp', '800x600'), 'image/webp')
    put(s3, 'demo-media', 'images/gradient.gif', image('gradients', '.gif', '480x270'), 'image/gif')
    put(s3, 'demo-media', 'images/logo.svg',
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><rect width="120" height="120" rx="24" '
        b'fill="#101626"/><circle cx="60" cy="60" r="34" fill="none" stroke="#f5a623" stroke-width="8"/>'
        b'<path d="M48 40l28 20-28 20z" fill="#f5a623"/></svg>', 'image/svg+xml')
    put(s3, 'demo-media', 'videos/test-pattern.mp4', video_mp4(), 'video/mp4')
    put(s3, 'demo-media', 'videos/fractal.webm', video_webm(), 'video/webm')
    put(s3, 'demo-media', 'audio/tone-440hz.mp3', audio('.mp3', 440, ('-c:a', 'libmp3lame', '-b:a', '96k')), 'audio/mpeg')
    put(s3, 'demo-media', 'audio/tone-660hz.ogg', audio('.ogg', 660, ('-c:a', 'libvorbis')), 'audio/ogg')
    put(s3, 'demo-media', 'audio/tone-330hz.wav', audio('.wav', 330, ('-ar', '22050', '-ac', '1')), 'audio/wav')

    # --- documents
    ensure_bucket(s3, 'demo-documents')
    put(s3, 'demo-documents', 'reports/2026/quarterly-report.pdf', pdf('Quarterly report (sample)', [
        'This PDF was generated for the S3FastBrowser demo.', '',
        'Summary', '  - Storage used: 1.2 TB (+4% quarter on quarter)',
        '  - Objects: 3.4 million', '  - Buckets: 12', '',
        'All figures in this document are invented.']), 'application/pdf',
        metadata={'department': 'example', 'reviewed': 'yes'})
    put(s3, 'demo-documents', 'reports/2026/handbook.pdf', pdf('Handbook (sample)', [LOREM[:90], LOREM[90:]]),
        'application/pdf')
    put(s3, 'demo-documents', 'spreadsheets/inventory.xlsx', xlsx(),
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    put(s3, 'demo-documents', 'spreadsheets/sales.csv', csv_data(), 'text/csv')
    put(s3, 'demo-documents', 'notes/README.md', MD_SAMPLE, 'text/markdown')
    put(s3, 'demo-documents', 'notes/meeting-summary.rtf', RTF_SAMPLE, 'application/rtf')
    put(s3, 'demo-documents', 'notes/plain.txt', (LOREM + '\n') * 12, 'text/plain')
    put(s3, 'demo-documents', 'web/index.html', HTML_SAMPLE, 'text/html')

    # --- code & config
    ensure_bucket(s3, 'demo-code')
    put(s3, 'demo-code', 'scripts/report.py', PY_SAMPLE, 'text/x-python')
    put(s3, 'demo-code', 'scripts/rotate-logs.sh', SH_SAMPLE, 'application/x-sh')
    put(s3, 'demo-code', 'config/settings.json', JSON_SAMPLE, 'application/json')
    put(s3, 'demo-code', 'config/pipeline.yaml', YAML_SAMPLE, 'application/x-yaml')
    put(s3, 'demo-code', 'logs/app-2026-01-01.log', log_data(), 'text/plain')
    put(s3, 'demo-code', 'misc/VERSION', b'demo-build 7\nchannel stable\n')  # no extension
    put(s3, 'demo-code', 'misc/random.bin', bytes(random.getrandbits(8) for _ in range(4096)),
        'application/octet-stream')

    # --- many objects (pagination)
    ensure_bucket(s3, 'demo-backups')
    for i in range(1, 121):
        d = date(2026, 1, 1) + timedelta(days=i)
        put(s3, 'demo-backups', f'daily/{d:%Y/%m}/backup-{d}.json',
            json.dumps({'date': str(d), 'files': random.randint(100, 900), 'status': 'ok'}).encode(),
            'application/json')

    # --- versioning, delete markers, policy, lifecycle
    ensure_bucket(s3, 'demo-versioned', versioning=True)
    for v in range(1, 4):
        put(s3, 'demo-versioned', 'contracts/agreement.txt',
            f'Sample agreement - revision {v}\n\n{LOREM}\n'.encode(), 'text/plain')
    put(s3, 'demo-versioned', 'contracts/old-draft.txt', b'An old draft that was deleted.\n', 'text/plain')
    s3.delete_object(Bucket='demo-versioned', Key='contracts/old-draft.txt')  # leaves a delete marker
    put(s3, 'demo-versioned', 'images/badge.png', image('testsrc', '.png', '320x240'), 'image/png')
    s3.put_bucket_lifecycle_configuration(Bucket='demo-versioned', LifecycleConfiguration={'Rules': [
        {'ID': 'expire-tmp', 'Status': 'Enabled', 'Filter': {'Prefix': 'tmp/'}, 'Expiration': {'Days': 7}},
        {'ID': 'old-versions', 'Status': 'Enabled', 'Filter': {'Prefix': ''},
         'NoncurrentVersionExpiration': {'NoncurrentDays': 90}},
    ]})
    s3.put_bucket_policy(Bucket='demo-versioned', Policy=json.dumps({
        'Version': '2012-10-17',
        'Statement': [{'Sid': 'PublicReadImages', 'Effect': 'Allow', 'Principal': {'AWS': ['*']},
                       'Action': ['s3:GetObject'], 'Resource': ['arn:aws:s3:::demo-versioned/images/*']}],
    }))
    print('demo data ready', flush=True)


if __name__ == '__main__':
    main()
