# syntax=docker/dockerfile:1
# Frontend-Image: Stufe 1 baut die SPA mit Node, Stufe 2 liefert sie über nginx (unprivilegiert, Port 8080).
# Keine externen Ressourcen zur Laufzeit; der Build lädt nur npm-Pakete (lockfile-fixiert).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Gleiche Prüfungen wie in der CI: ein Build mit externen URLs scheitert bereits hier.
RUN npm run build && npm run check:external

FROM nginxinc/nginx-unprivileged:1.29-alpine
USER root
# Cache der Kachel-Proxys (Named Volume erbt Besitzer und Rechte dieses Verzeichnisses)
RUN mkdir -p /var/cache/tiles && chown 101:101 /var/cache/tiles
USER 101
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
# nginx-unprivileged läuft als Benutzer "nginx" (UID 101), nicht als root.
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
