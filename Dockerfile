FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    S3FB_DATA_DIR=/data \
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=8 \
    GUNICORN_TIMEOUT=300

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION LICENSE ./
COPY src ./src
COPY templates ./templates
COPY static ./static

RUN useradd --system --uid 10001 --home-dir /app s3fb \
 && mkdir -p /data \
 && chown s3fb:s3fb /data
USER s3fb
VOLUME ["/data"]

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/healthz', timeout=4)" || exit 1

# gthread workers: long ZIP/media streams do not block the whole worker.
CMD ["sh", "-c", "exec gunicorn --chdir src --bind 0.0.0.0:5001 --worker-class gthread --workers ${GUNICORN_WORKERS} --threads ${GUNICORN_THREADS} --timeout ${GUNICORN_TIMEOUT} --access-logfile - --error-logfile - 'app:create_app()'"]
