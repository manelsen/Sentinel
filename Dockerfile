FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md sentinel.toml /app/
COPY src /app/src

RUN pip install --no-cache-dir .

EXPOSE 8080

ENTRYPOINT ["sentinel"]
CMD ["serve", "--config", "/app/sentinel.toml", "--db", "/data/sentinel.db", "--host", "0.0.0.0", "--port", "8080"]
