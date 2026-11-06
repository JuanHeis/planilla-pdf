FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLANILLA_MODE=web \
    PLANILLA_DATA_DIR=/data \
    PLANILLA_OCR_STORE=/data/aprendizaje-ocr.sqlite3

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py LICENSE NOTICE LEEME.md REQUIREMENTS.md DEPLOY.md ./
COPY static ./static
COPY tessdata ./tessdata
RUN mkdir -p /data

EXPOSE 8080
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 1 --timeout 120 --max-requests 200 --max-requests-jitter 50 --access-logfile - --error-logfile - server:app"]
