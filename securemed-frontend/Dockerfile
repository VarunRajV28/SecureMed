# 1. Install dependencies
FROM oven/bun:1 AS deps
WORKDIR /app
COPY package.json bun.lock ./
# Relaxed install to prevent lockfile mismatch errors
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

# Explicitly set production
ENV NODE_ENV=production \
    PORT=8080 \
    HOSTNAME=0.0.0.0

# Install shadow utility for user management
RUN apt-get update \
    && apt-get install -y --no-install-recommends shadow \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid 1001 nextjs

# Copy standalone build files
COPY --from=builder --chown=nextjs:nodejs /app/public               ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone      ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static          ./.next/static

USER nextjs
EXPOSE 8080

# Production start command
CMD ["node", "server.js"]
