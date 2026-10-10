# Newman: the hub (Python) + its web dashboard (built with Vite), one image.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build \
    # Compressed copies next to each script / style: sent to browsers that accept them (much faster away from home).
 && find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.svg' -o -name '*.html' \) -size +1k -exec gzip -k -9 {} +

FROM python:3.12-slim
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8093 \
    DATA_DIR=/data \
    WEB_DIR=/app/web

WORKDIR /app
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server/newman/ ./newman/
COPY --from=web /web/dist/ ./web/

RUN mkdir -p /data
VOLUME /data
EXPOSE 8093

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/health',timeout=4)" || exit 1

CMD ["python", "-m", "newman"]
