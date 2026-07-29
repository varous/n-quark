FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv pip install --system .

ENV NQUARK_PORT=8004
EXPOSE 8004

CMD ["uvicorn", "observation_service.main:app", "--host", "0.0.0.0", "--port", "8004"]
