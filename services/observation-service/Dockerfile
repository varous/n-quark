FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv pip install --system . && chmod +x scripts/entrypoint.sh

ENV NQUARK_PORT=8004
EXPOSE 8004

CMD ["scripts/entrypoint.sh"]
