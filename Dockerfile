FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SQLITE_PATH=/data/cards.db

WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data && chown app:app /data

COPY pyproject.toml README.md ./
COPY src/ ./src/
# debugpy is inert unless explicitly selected by the local debug override.
# Editable installation keeps debugger paths at /app/src, matching local source.
RUN python -m pip install --no-cache-dir -e '.[infra]' 'debugpy>=1.8,<2'

USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "card_servicing.main:app", "--host", "0.0.0.0", "--port", "8000"]
