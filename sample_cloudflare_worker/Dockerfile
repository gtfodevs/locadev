# Cloudflare Workers local emulator (workerd via wrangler) — multi-arch Node.
# Default build context for locadev profile `cloudflare`.
# Override with CLOUDFLARE_WORKER_DIR (e.g. ../gigchain/auth) to run another Worker.
FROM node:20-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN chmod +x docker-entrypoint.sh 2>/dev/null || true

ENV HOST=0.0.0.0 \
    PORT=8787 \
    ISSUER=http://127.0.0.1:8787

EXPOSE 8787

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["npx", "wrangler", "dev", "--ip", "0.0.0.0", "--port", "8787", "--local"]
