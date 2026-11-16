FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SQLITE_PATH=/data/cards.db \
    PYTHONPATH=/app/src

WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /data /home/app/.cache && chown -R app:app /data /home/app

COPY pyproject.toml ./
RUN python -c "import subprocess,sys,tomllib; p=tomllib.load(open('pyproject.toml','rb'))['project']; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir']+p['dependencies']+p['optional-dependencies']['infra']+p['optional-dependencies']['chat'])"
ENV HF_HOME=/home/app/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed
RUN python -c "from fastembed import TextEmbedding; list(TextEmbedding(model_name='BAAI/bge-small-en-v1.5').embed(['warmup']))" \
    && chown -R app:app /home/app
COPY README.md ./
COPY src/ ./src/
COPY policies/ ./policies/
COPY docs/cards.openapi.json ./docs/cards.openapi.json
COPY scripts/service_entrypoint.py ./scripts/service_entrypoint.py
# PYTHONPATH preserves /app/src mappings without reinstalling on source edits.

USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "card_servicing.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS debug
USER root
RUN python -m pip install --no-cache-dir 'debugpy>=1.8,<2'
USER app
