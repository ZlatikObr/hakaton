FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ_DATA_DIR=/data
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml /app/
COPY tz_audit /app/tz_audit
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home auditor && mkdir /data && chown auditor:auditor /data
USER auditor
EXPOSE 8000
CMD ["uvicorn", "tz_audit.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
