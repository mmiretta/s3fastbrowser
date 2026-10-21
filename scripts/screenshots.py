#!/usr/bin/env python3
"""Take the documentation screenshots from a running demo instance.

    docker compose --profile demo up -d s3fastbrowser-demo
    pip install playwright && playwright install chromium   # or pass --chrome
    python scripts/screenshots.py --url http://127.0.0.1:5002 --out docs/screenshots

Only use it against the demo (generated data), never against real storage.
It also fails if the browser console reports JavaScript errors.
"""
import argparse
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

VIEWPORT = {'width': 1440, 'height': 900}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://127.0.0.1:5002')
    ap.add_argument('--out', default='docs/screenshots')
    ap.add_argument('--chrome', default=None, help='path to a Chrome/Chromium binary')
    ap.add_argument('--user', default='demo')
    ap.add_argument('--password', default='demo')
    ap.add_argument('--headed', action='store_true',
                    help="run a headed browser (e.g. under xvfb-run): headless Chrome does not render "
                         "its built-in PDF viewer, so use this for the PDF screenshot")
    ap.add_argument('--only', action='append', default=[],
                    help='only save these file names (can be repeated)')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    errors = []

    with sync_playwright() as p:
        # '--headless=new': recent Chrome builds no longer ship the old headless mode.
        launch_args = ['--autoplay-policy=no-user-gesture-required']
        if not args.headed:
            launch_args.append('--headless=new')
        browser = p.chromium.launch(executable_path=args.chrome, headless=False, args=launch_args)
        ctx = browser.new_context(viewport=VIEWPORT, color_scheme='dark')
        page = ctx.new_page()
        # The sandboxed HTML preview blocks the sample page's <script>: that is
        # the expected behaviour, not an error.
        page.on('console', lambda m: errors.append(f'console {m.type}: {m.text}')
                if m.type == 'error' and 'allow-scripts' not in m.text else None)
        page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

        def shot(name, full_page=False, locator=None):
            if args.only and name not in args.only:
                return
            path = os.path.join(args.out, name)
            page.wait_for_timeout(400)
            if locator:
                page.locator(locator).screenshot(path=path)
            else:
                page.screenshot(path=path, full_page=full_page)
            print('saved', path)

        def js(code):
            page.evaluate(code)
            page.wait_for_load_state('networkidle')

        def wait_js(expr, timeout=20.0):
            # page.wait_for_function() evaluates a string inside the page, which
            # the app's Content-Security-Policy (no 'unsafe-eval') forbids, so
            # poll from here instead.
            import time
            deadline = time.time() + timeout
            while time.time() < deadline:
                if page.evaluate(expr):
                    return
                page.wait_for_timeout(250)
            raise TimeoutError(expr)

        def close_modals():
            page.evaluate("document.querySelectorAll('.modal.show').forEach(m => bootstrap.Modal.getInstance(m).hide())")
            page.wait_for_timeout(500)

        def preview(bucket, prefix, key, name, wait=None):
            if args.only and name not in args.only:
                return
            js(f"selectBucket({bucket!r})")
            js(f"navigateTo({prefix!r})")
            js(f"previewObject({key!r})")
            page.wait_for_selector('#preview-modal-body .pv-plugin-tag, #preview-modal-body .alert', timeout=15000)
            if wait:
                wait_js(wait)
            if name.endswith('pdf.png'):
                page.wait_for_timeout(3000)  # let the PDF viewer paint
            shot(name)
            close_modals()

        # --- login
        page.goto(args.url + '/')
        page.wait_for_selector('#password')
        page.fill('#password', args.password)
        shot('01-login.png')
        page.fill('#username', args.user)
        page.click('button[type=submit]')
        page.wait_for_selector('#s3-connection-form')
        page.evaluate("fetch('/disconnect', {method: 'POST'})")
        page.reload()
        page.wait_for_selector('#demo-connect-btn')
        shot('02-connect.png')

        # --- connect & browse
        page.click('#demo-connect-btn')
        page.wait_for_selector('#bucket-list [data-bucket]')
        js("selectBucket('demo-media')")
        page.wait_for_selector('#object-list .object-checkbox')
        shot('03-buckets.png')
        js("navigateTo('images/')")
        page.wait_for_selector('[data-object-key="images/test-pattern.png"]')
        shot('04-object-browser.png')

        # --- object details (metadata, tags, presigned URL)
        js("showObjectDetails('images/test-pattern.png')")
        page.wait_for_selector('#objectModal.show')
        page.click('#objectModal button:has-text("Generate")')
        page.wait_for_selector('#presign-result:not(.d-none)')
        shot('05-object-details.png')
        close_modals()

        # --- previews, one per plugin
        preview('demo-media', 'images/', 'images/test-pattern.png', '10-preview-image.png')
        preview('demo-documents', 'reports/2026/', 'reports/2026/quarterly-report.pdf', '11-preview-pdf.png')
        page.wait_for_timeout(500)
        preview('demo-media', 'videos/', 'videos/test-pattern.mp4', '12-preview-video.png',
                wait="(() => { const v = document.querySelector('#preview-modal-body video');"
                     " if (!v) return false; if (v.readyState >= 2 && v.currentTime < 1) v.currentTime = 2.5;"
                     " return v.readyState >= 2 && v.currentTime >= 2; })()")
        preview('demo-media', 'audio/', 'audio/tone-440hz.mp3', '13-preview-audio.png',
                wait="(() => { const a = document.querySelector('#preview-modal-body audio'); return a && a.readyState >= 1; })()")
        preview('demo-documents', 'notes/', 'notes/README.md', '14-preview-text.png')
        preview('demo-code', 'scripts/', 'scripts/report.py', '15-preview-python.png')
        preview('demo-code', 'scripts/', 'scripts/rotate-logs.sh', '16-preview-shell.png')
        preview('demo-code', 'config/', 'config/settings.json', '17-preview-json.png')
        preview('demo-documents', 'spreadsheets/', 'spreadsheets/sales.csv', '18-preview-csv.png')
        preview('demo-documents', 'spreadsheets/', 'spreadsheets/inventory.xlsx', '19-preview-xlsx.png')
        preview('demo-documents', 'web/', 'web/index.html', '20-preview-html.png')
        preview('demo-documents', 'notes/', 'notes/meeting-summary.rtf', '21-preview-rtf.png')
        preview('demo-code', 'misc/', 'misc/random.bin', '22-preview-fallback-hex.png')
        preview('demo-code', 'misc/', 'misc/VERSION', '23-preview-fallback-text.png')

        # --- search
        js("selectBucket('demo-backups')")
        page.fill('#search-input', '2026-03-1')
        js("searchFiles()")
        page.wait_for_selector('#search-results-count:has-text("Found")')
        shot('30-search.png')
        js("clearSearch()")

        # --- upload dialog with a few local files selected
        js("selectBucket('demo-documents')")
        js("navigateTo('notes/')")
        js("showUploadModal()")
        page.wait_for_selector('#uploadModal.show')
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for name, size in (('draft-notes.txt', 2400), ('diagram.png', 48000), ('budget.xlsx', 15500)):
                path = os.path.join(tmp, name)
                with open(path, 'wb') as fh:
                    fh.write(b'x' * size)
                files.append(path)
            page.set_input_files('#upload-file', files)
            page.wait_for_timeout(300)
            shot('31-upload.png')
        close_modals()

        # --- settings modal
        js("openSettingsModal()")
        page.wait_for_selector('#settingsModal.show')
        shot('32-settings.png')
        close_modals()

        # --- versions + delete markers
        page.evaluate("fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                      " body: JSON.stringify(Object.assign({}, window.appSettings, {show_delete_markers: true}))})")
        page.reload()
        page.wait_for_selector('#bucket-list [data-bucket]')
        js("selectBucket('demo-versioned')")
        js("navigateTo('contracts/')")
        page.wait_for_selector('text=Delete Marker')
        shot('33-delete-markers.png')
        js("showObjectDetails('contracts/agreement.txt')")
        page.wait_for_selector('#objectModal.show')
        shot('34-versions.png')
        close_modals()
        page.evaluate("fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                      " body: JSON.stringify(Object.assign({}, window.appSettings, {show_delete_markers: false}))})")

        # --- bucket settings page (policy + lifecycle)
        page.goto(args.url + '/bucket/demo-versioned/settings')
        wait_js("document.getElementById('lifecycle-json').value.length > 0")
        page.click('#view-tab')
        shot('35-bucket-settings.png', full_page=True)

        # --- light theme
        page.goto(args.url + '/')
        page.wait_for_selector('#bucket-list [data-bucket]')
        page.click('#theme-toggle')
        js("selectBucket('demo-documents')")
        js("navigateTo('reports/2026/')")
        page.wait_for_selector('[data-object-key="reports/2026/quarterly-report.pdf"]')
        shot('36-light-theme.png')
        page.click('#theme-toggle')

        browser.close()

    if errors:
        print('\nBrowser errors:\n  ' + '\n  '.join(errors), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
