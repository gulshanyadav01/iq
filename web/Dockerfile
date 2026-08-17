# ---------------- Base Image Setup ----------------
FROM --platform=${TARGETPLATFORM:-linux/amd64} node:24-alpine AS base

RUN apk update && apk upgrade --no-cache libc6-compat libcrypto3 libssl3
RUN corepack enable
RUN corepack prepare pnpm@10.15.0 --activate

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
ARG TARGETARCH

# ---------------- Install Dependencies ----------------
# Manifests only, so this layer is reused on every source-only change. The
# workspace has a single package (`web`), so there is nothing to prune — an
# earlier revision ran `turbo prune` plus a second `pnpm install` in the
# builder stage, which doubled install work for no benefit.
FROM base AS deps

WORKDIR /mono
ARG TARGETARCH

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json ./web/

RUN --mount=type=cache,id=pnpm-${TARGETARCH},target=/pnpm/store,sharing=locked \
    pnpm install --frozen-lockfile

# ---------------- Build Nuxt App ----------------
FROM base AS builder

WORKDIR /mono
ARG TARGETARCH

COPY --from=deps /mono/package.json /mono/pnpm-lock.yaml /mono/pnpm-workspace.yaml ./
COPY --from=deps /mono/node_modules ./node_modules
COPY --from=deps /mono/web/node_modules ./web/node_modules

# Only the web package is copied: the build context still contains the Django
# backend and infra dirs (the backend image shares context `.`), and copying
# them here would invalidate this layer on every backend-only commit.
# web/Dockerfile.dockerignore keeps them out of the context entirely.
COPY web ./web

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

# Vite/nitro/rollup scratch caches survive across builds via a cache mount, so
# an incremental rebuild does not redo the whole transform pass. Not baked into
# the layer — .output is what the runner copies.
RUN --mount=type=cache,id=nuxt-${TARGETARCH},target=/mono/web/node_modules/.cache,sharing=locked \
    --mount=type=cache,id=nuxt-vite-${TARGETARCH},target=/mono/web/node_modules/.vite,sharing=locked \
    pnpm run build

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
