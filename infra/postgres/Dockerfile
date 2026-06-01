FROM postgres:16.13-alpine

RUN apk upgrade --no-cache \
    && apk add --no-cache su-exec \
    && sed -i 's/gosu /su-exec /g' /usr/local/bin/docker-entrypoint.sh \
    && rm -f /usr/local/bin/gosu

USER postgres

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD pg_isready || exit 1
