# One-shot container that fills the demo MinIO with generated sample data.
# Everything is synthesised here (ffmpeg test patterns, tones, lorem ipsum):
# no real files are involved.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir boto3==1.35.99 openpyxl==3.1.5
WORKDIR /seed
COPY seed.py .
CMD ["python", "seed.py"]
