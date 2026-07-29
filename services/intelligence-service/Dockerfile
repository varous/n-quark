FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --timeout=120 --retries=5 uv

COPY . .
RUN UV_HTTP_TIMEOUT=120 uv pip install --system .

ENV NQUARK_PORT=8009
EXPOSE 8009

CMD ["uvicorn", "intelligence_service.main:app", "--host", "0.0.0.0", "--port", "8009"]
