# 1. Install dependencies
FROM oven/bun:1 AS deps
WORKDIR /app
COPY package.json bun.lock ./
# Relaxed install to prevent lockfile mismatch errors in the cloud
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

# Set production environment explicitly
ENV NODE_ENV=production \
    PORT=8080 \
    HOSTNAME=0.0.0.0

# Install shadow utility and set up the nextjs user for security
RUN apt-get update \
    && apt-get install -y --no-install-recommends shadow \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid 1001 nextjs

# Copy the 'standalone' build files from the builder stage
# This is much smaller and faster than copying the whole app
COPY --from=builder --chown=nextjs:nodejs /app/public               ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone      ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static          ./.next/static

USER nextjs
EXPOSE 8080

# The only CMD in the file. Railway will use this for the production server.
CMD ["node", "server.js"]
