# 1. Install dependencies
FROM oven/bun:1 AS deps
WORKDIR /app
COPY package.json bun.lock ./
# Keep your relaxed install to prevent lockfile mismatch errors in the cloud
RUN bun install

# 2. Production build
FROM deps AS builder
WORKDIR /app
COPY . .
ARG BACKEND_URL
ENV BACKEND_URL=$BACKEND_URL
RUN bun run build

# ── dev (Moved here so it's not the default) ──────────────────────────────────
FROM oven/bun:1 AS dev
WORKDIR /app

ENV NODE_ENV=development \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY package.json bun.lock ./
RUN bun install

COPY . .

EXPOSE 3000
CMD ["bun", "run", "dev"]

# 3. Production runtime (Now the final, default stage) ─────────────────────────
FROM oven/bun:1 AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=8080 \
    HOSTNAME=0.0.0.0

# Added shadow utility from main repo to ensure groupadd/useradd works perfectly
RUN apt-get update \
    && apt-get install -y --no-install-recommends shadow \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid 1001 nextjs

# Copy standalone build files with correct ownership
COPY --from=builder --chown=nextjs:nodejs /app/public               ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone      ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static          ./.next/static

USER nextjs
EXPOSE 8080
# This is the final CMD that Railway will execute by default
CMD ["node", "server.js"]
