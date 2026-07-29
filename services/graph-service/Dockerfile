FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv pip install --system .

ENV NQUARK_PORT=8006
EXPOSE 8006

CMD ["uvicorn", "graph_service.main:app", "--host", "0.0.0.0", "--port", "8006"]
