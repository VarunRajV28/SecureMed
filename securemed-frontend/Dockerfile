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

# 3. Production runtime
FROM oven/bun:1 AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=8080 \
    HOSTNAME=0.0.0.0

# groupadd/useradd are pre-installed on oven/bun:1 (Debian-based) — no extra packages needed
RUN groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid 1001 nextjs

# Copy standalone build files with correct ownership
COPY --from=builder --chown=nextjs:nodejs /app/public               ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone      ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static          ./.next/static

USER nextjs
EXPOSE 8080
CMD ["node", "server.js"]

# ── dev (New stage from main repo) ────────────────────────────────────────────
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