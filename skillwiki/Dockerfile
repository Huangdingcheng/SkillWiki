FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir -e ".[api]"
COPY docker-entrypoint.sh /usr/local/bin/skillwiki-entrypoint
RUN chmod +x /usr/local/bin/skillwiki-entrypoint
ENV SKILLOS_GOVERNANCE_REPO=/data/governance
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/skillwiki-entrypoint"]
CMD ["skillwiki", "serve", "--host", "0.0.0.0", "--port", "8000", "--backend", "git", "--storage-dir", "/data/skills", "--governance-repo", "/data/governance", "--llm-retries", "0", "--no-seed-demo"]
