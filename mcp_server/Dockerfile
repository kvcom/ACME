FROM python:3.13-alpine
WORKDIR /app
COPY . /app
RUN apk upgrade --no-cache \
    && python -m pip install --no-cache-dir --upgrade "pip>=26.1" \
    && python -m pip install --no-cache-dir -e . \
    && addgroup -S app \
    && adduser -S -G app -h /home/app app
USER app
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"
CMD ["python", "-m", "acme_mcp.server"]
