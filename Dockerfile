# Calendar Agent — üretim imajı (yalnızca LLM_PROVIDER=gemini destekler).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# Önce bağımlılıklar (katman önbelleği: kod değişince yeniden kurulmaz).
COPY requirements-docker.txt .
RUN pip install -r requirements-docker.txt

COPY pyproject.toml ./
COPY src/ src/

# Root olmayan kullanıcı; data/ volume'u bu kullanıcıya ait olmalı.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data
USER app

VOLUME /app/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/saglik', timeout=4).status==200 else 1)"

# TEK worker şart: çift-tıklama korumaları bellek-içi, tarama thread'i süreç içi.
CMD ["python", "-m", "src.ui.app"]
