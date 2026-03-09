FROM oven/bun:1 AS deps

WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# ── production build ──────────────────────────────────────────────────────────
FROM deps AS builder

COPY . .
ARG BACKEND_URL
ENV BACKEND_URL=$BACKEND_URL
# This pre-compiles the site so it loads instantly later
RUN bun run build

# ── production runtime ────────────────────────────────────────────────────────
FROM oven/bun:1 AS runner

WORKDIR /app

ENV NODE_ENV=production \
    PORT=8080 \
    HOSTNAME=0.0.0.0

# Alpine-compatible user and group creation
RUN addgroup -S -g 1001 nodejs && \
    adduser -S -u 1001 -G nodejs nextjs

# Copy the standalone build files
COPY --from=builder /app/public               ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone      ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static          ./.next/static

USER nextjs
EXPOSE 8080
# Use node to run the standalone server for best performance
CMD ["node", "server.js"]
