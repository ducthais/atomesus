# Web Next.js cho deploy/compose.yml. Gọi backend qua mạng nội bộ của compose (PIPELINE_BACKEND_URL).
FROM node:24-slim AS build
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# next.config.ts đọc biến này lúc build để dựng rewrite /api/v1 → backend.
ARG PIPELINE_BACKEND_URL=http://backend:8000
ENV PIPELINE_BACKEND_URL=$PIPELINE_BACKEND_URL
RUN npm run build && npm prune --omit=dev

FROM node:24-slim
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
COPY --from=build --chown=node:node /web ./
USER node
EXPOSE 3000
CMD ["npm", "start"]
