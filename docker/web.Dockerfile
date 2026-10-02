# Dev image: Node 22 + pnpm with dependencies AND source baked in.
# No bind mounts (Docker Desktop cannot share the SSD volume this repo lives
# on) — `make up` rebuilds the image; for live reload run `make dev-web` on
# the host instead.
FROM node:22-alpine

RUN corepack enable

WORKDIR /app

# Dependencies first (cached between source edits). The pnpm store is a
# BuildKit cache mount so a retried build does not re-download tarballs.
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN --mount=type=cache,id=erp-pnpm-store,target=/pnpm/store \
    npm_config_store_dir=/pnpm/store pnpm approve-builds --all \
 && npm_config_store_dir=/pnpm/store pnpm install --ignore-scripts

COPY web/ .

EXPOSE 5173

CMD ["pnpm", "dev"]
