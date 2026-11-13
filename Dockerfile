# Single-port Hugging Face Docker Space; local Compose remains available.
FROM node:22-alpine AS frontend
WORKDIR /build
ENV PUPPETEER_SKIP_DOWNLOAD=true
COPY skillwiki-frontend/package*.json ./
RUN sed -i 's|https://registry.npmmirror.com/|https://registry.npmjs.org/|g' package-lock.json \
    && npm ci --registry=https://registry.npmjs.org --fetch-retries=2
COPY skillwiki-frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends git nginx ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --user-group user \
    && mkdir -p /app/backend /data /app/space /app/frontend \
    && chown -R user:user /app /data
WORKDIR /app/backend
COPY --chown=user:user LICENSE /app/LICENSE
COPY --chown=user:user skillwiki/pyproject.toml skillwiki/README.md ./
COPY --chown=user:user skillwiki/skillwiki ./skillwiki
RUN pip install --no-cache-dir --disable-pip-version-check -e '.[api]' 'uvicorn[standard]>=0.20'
COPY --from=frontend --chown=user:user /build/dist /app/frontend
COPY --chown=user:user deploy/huggingface/start.py deploy/huggingface/nginx.conf /app/space/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/home/user \
    LLM_API_URL=https://api.deepseek.com LLM_MODEL=deepseek-flash \
    LLM_MAX_TOKENS=16384 LLM_TIMEOUT=120 SPACE_DATA_DIR=/data
USER 1000:1000
WORKDIR /data
EXPOSE 7860
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:7860/health', timeout=3)"
ENTRYPOINT ["python", "/app/space/start.py"]
