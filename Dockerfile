# Lightweight container for FastAPI dashboard

FROM python:3.12-slim

RUN useradd -m -s /bin/bash agent

WORKDIR /app

COPY web-dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY web-dashboard/ .

RUN chown -R agent:agent /app

USER agent

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
