# Production web image — static SPA build + nginx proxying /api to the API.
FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /app
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
# pnpm 12 blocks dependency build scripts unless approved (esbuild).
RUN pnpm approve-builds --all && pnpm install --ignore-scripts
COPY web/ .
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL
RUN pnpm build

FROM nginxinc/nginx-unprivileged:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
