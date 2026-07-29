FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv pip install --system .

ENV NQUARK_PORT=8002
EXPOSE 8002

CMD ["uvicorn", "media_service.main:app", "--host", "0.0.0.0", "--port", "8002"]
