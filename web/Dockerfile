# ---------------- Base Image Setup ----------------
FROM --platform=${TARGETPLATFORM:-linux/amd64} node:24-alpine AS base

RUN apk update && apk upgrade --no-cache libc6-compat libcrypto3 libssl3
RUN corepack enable
RUN corepack prepare pnpm@10.15.0 --activate

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
ARG TARGETARCH

# ---------------- Prune Dependencies ----------------
FROM base AS pruner

WORKDIR /mono
ARG TARGETARCH

# Copy only package management files first (for better layer caching)
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json ./web/

# Install dependencies (pnpm store cached across stages/builds via BuildKit cache mount)
RUN --mount=type=cache,id=pnpm-${TARGETARCH},target=/pnpm/store,sharing=locked \
    pnpm install --frozen-lockfile

# Now copy the rest of the source code
COPY . .

RUN npx turbo prune --scope=web --docker

# ---------------- Build Nuxt App ----------------
FROM base AS builder

WORKDIR /mono
ARG TARGETARCH

COPY --from=pruner /mono/out/json ./
COPY --from=pruner /mono/out/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=pruner /mono/out/pnpm-lock.yaml ./pnpm-lock.yaml
RUN --mount=type=cache,id=pnpm-${TARGETARCH},target=/pnpm/store,sharing=locked \
    pnpm install --frozen-lockfile

COPY --from=pruner /mono/out/full ./
WORKDIR /mono/web

# Build-time environment variables (with defaults for build phase)
# These are placeholder values that will be overridden at runtime via config.js
ARG NUXT_PUBLIC_AUTH_BASE_URL=http://localhost:10000
ARG NUXT_PUBLIC_APP_BASE_URL=http://localhost:3000
ARG NUXT_PUBLIC_API_SCHEME=http
ARG NUXT_PUBLIC_API_BASE_URL=localhost:8000
ARG NUXT_PUBLIC_DEFAULT_PROFILE_URL=
ARG NUXT_APP_BASE_URL=/

ENV NUXT_PUBLIC_AUTH_BASE_URL=$NUXT_PUBLIC_AUTH_BASE_URL
ENV NUXT_PUBLIC_APP_BASE_URL=$NUXT_PUBLIC_APP_BASE_URL
ENV NUXT_PUBLIC_API_SCHEME=$NUXT_PUBLIC_API_SCHEME
ENV NUXT_PUBLIC_API_BASE_URL=$NUXT_PUBLIC_API_BASE_URL
ENV NUXT_PUBLIC_DEFAULT_PROFILE_URL=$NUXT_PUBLIC_DEFAULT_PROFILE_URL
ENV NUXT_APP_BASE_URL=$NUXT_APP_BASE_URL

RUN pnpm run build

# ---------------- Runtime Image ----------------
FROM node:24-alpine AS runner

RUN apk add --no-cache dumb-init dos2unix

ENV NODE_ENV=production
ENV NUXT_HOST=0.0.0.0
ENV NUXT_PORT=3000

# Use non-root user
ARG UID=1001
ARG GID=1001
RUN addgroup --system --gid ${GID} webuser \
    && adduser --system --uid ${UID} --ingroup webuser webuser
USER webuser

WORKDIR /app

COPY --from=builder --chown=webuser:webuser /mono/web/.output ./.output
COPY --from=builder --chown=webuser:webuser /mono/web/entrypoint.sh .

# Convert line endings to Unix format (safety measure for Windows)
RUN dos2unix entrypoint.sh && chmod +x entrypoint.sh

EXPOSE 3000

ENTRYPOINT ["dumb-init", "--", "sh", "./entrypoint.sh"]
