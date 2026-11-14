# Квантара.Закупки — сервис проверки технических заданий на закупку.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ_DATA_DIR=/data

WORKDIR /app

# Зависимости ставятся отдельным слоем: при правке кода они не пересобираются.
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml /app/
COPY tz_audit /app/tz_audit
RUN pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home auditor \
    && mkdir /data && chown auditor:auditor /data

USER auditor
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "tz_audit.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
