FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/data/models CHROMA_PATH=/data/chroma AUDIT_PATH=/data/audit.sqlite3
WORKDIR /app
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
COPY requirements-service.txt .
RUN pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements-service.txt
COPY --chown=app:app . .
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"
CMD ["python", "-m", "uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "16"]
