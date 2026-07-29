FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --timeout=120 --retries=5 uv

COPY . .
RUN UV_HTTP_TIMEOUT=120 uv pip install --system .

ENV NQUARK_PORT=8001
EXPOSE 8001

CMD ["uvicorn", "crawl_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
