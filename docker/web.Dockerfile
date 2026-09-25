# Dev image: Node 22 + pnpm (version pinned via web/package.json packageManager).
# Source is bind-mounted by docker-compose.yml; node_modules lives in a named volume.
FROM node:22-alpine

RUN corepack enable

WORKDIR /app
EXPOSE 5173

CMD ["sh", "-c", "pnpm approve-builds --all && pnpm install --ignore-scripts && pnpm dev"]
