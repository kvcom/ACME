FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir -e .[dev]
COPY . /app
EXPOSE 8000
CMD ["uvicorn", "acme_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
