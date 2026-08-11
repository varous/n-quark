# nquark-admin — the ONE public authenticated production console (Admin D).
# Builds the React SPA, then serves it + the /admin/v1 read-only BFF from the api-gateway FastAPI app,
# as a single image. BUILD CONTEXT IS THE REPO ROOT (needs both frontend/ and services/api-gateway/).
#   fly deploy --config deploy/fly/admin-console.toml --dockerfile deploy/fly/admin-console.Dockerfile .

# ---- stage 1: build the SPA (same-origin: the bundle calls /admin/v1 directly, no dev proxy) ----
FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Production build reads .env.production; empty VITE_API_URL => API base "/admin/v1" (same origin).
RUN printf 'VITE_API_URL=\n' > .env.production && npm run build

# ---- stage 2: the gateway image, with the built SPA baked in ----
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir --timeout=120 --retries=5 uv
COPY services/api-gateway/ ./
RUN UV_HTTP_TIMEOUT=120 uv pip install --system . && chmod +x scripts/entrypoint.sh
COPY --from=frontend /fe/dist ./frontend_dist
ENV NQUARK_PORT=8000
ENV NQUARK_ADMIN_FRONTEND_DIR=/app/frontend_dist
EXPOSE 8000
CMD ["scripts/entrypoint.sh"]
