FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv pip install --system .

ENV NQUARK_PORT=8005
EXPOSE 8005

CMD ["uvicorn", "entity_service.main:app", "--host", "0.0.0.0", "--port", "8005"]
