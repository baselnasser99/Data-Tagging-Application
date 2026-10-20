FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user
RUN useradd --create-home appuser \
    && mkdir -p /app/application/static/uploads /app/logs \
    && chown -R appuser /app/application/static/uploads /app/logs
USER appuser

EXPOSE 5000
# --timeout 120: handing out 50,000 tasks takes about 43 s (scripts/benchmark.py), over Gunicorn's 30 s default.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "--timeout", "120", "app:app"]
