# Tiny cron image — stdlib only, no deps. Run as a Fly scheduled machine (see deploy/fly/README.md).
FROM python:3.12-slim
WORKDIR /app
COPY scheduled_ingest.py .
CMD ["python", "scheduled_ingest.py"]
