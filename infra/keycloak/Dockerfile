FROM alpine:3.23 AS keycloak-deps

RUN apk add --no-cache ca-certificates wget

WORKDIR /deps
RUN for artifact in netty-codec netty-codec-dns netty-codec-haproxy netty-codec-http netty-codec-http2 netty-codec-socks; do \
      wget -q -O "io.netty.${artifact}-4.1.132.Final.jar" "https://repo1.maven.org/maven2/io/netty/${artifact}/4.1.133.Final/${artifact}-4.1.133.Final.jar"; \
    done \
    && wget -q -O "io.netty.netty-handler-proxy-4.1.132.Final.jar" "https://repo1.maven.org/maven2/io/netty/netty-handler-proxy/4.1.133.Final/netty-handler-proxy-4.1.133.Final.jar" \
    && wget -q -O "io.opentelemetry.opentelemetry-api-1.57.0.jar" "https://repo1.maven.org/maven2/io/opentelemetry/opentelemetry-api/1.62.0/opentelemetry-api-1.62.0.jar"

FROM quay.io/keycloak/keycloak:26.6.2 AS keycloak-dist

FROM eclipse-temurin:21-jre-alpine

RUN apk upgrade --no-cache \
    && addgroup -S keycloak \
    && adduser -S -G keycloak -h /opt/keycloak keycloak

COPY --from=keycloak-dist --chown=keycloak:keycloak /opt/keycloak /opt/keycloak

USER root
COPY --from=keycloak-deps --chown=keycloak:keycloak /deps/*.jar /opt/keycloak/lib/lib/main/

USER keycloak
WORKDIR /opt/keycloak
RUN /opt/keycloak/bin/kc.sh build --db=dev-file

USER root
RUN rm -f /opt/keycloak/lib/lib/main/com.microsoft.sqlserver.mssql-jdbc-*.jar

USER keycloak

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD /opt/keycloak/bin/kc.sh show-config >/dev/null || exit 1

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
