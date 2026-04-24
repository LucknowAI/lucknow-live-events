FROM node:20-bookworm-slim

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml* /app/
RUN pnpm install --frozen-lockfile || pnpm install

COPY . /app

EXPOSE 3000

