FROM node:22-alpine AS build

WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2.10-alpine
COPY --from=build /web/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443
