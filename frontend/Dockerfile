FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS contract
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /workspace
COPY backend/pyproject.toml backend/requirements*.lock backend/install-locked.sh ./backend/
COPY backend/app ./backend/app
RUN bash backend/install-locked.sh runtime
COPY scripts/export-openapi.py ./scripts/export-openapi.py
RUN python -B scripts/export-openapi.py > /tmp/openapi.json

FROM node:24.18.1-bookworm-slim@sha256:235600a8101ab264e117b1768e925532262668dc9b581ef1dd7d96ced463b8e7 AS build
WORKDIR /workspace/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY --from=contract /tmp/openapi.json /tmp/openapi.json
ENV OPENAPI_SCHEMA_FILE=/tmp/openapi.json
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6 AS runtime
COPY frontend/nginx.conf /etc/nginx/nginx.conf
COPY frontend/functional-proxy.conf /etc/nginx/functional-proxy.conf
COPY --from=build /workspace/frontend/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
CMD ["nginx", "-g", "daemon off;"]
