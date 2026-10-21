FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system django \
    && adduser --system --ingroup django --home /app django

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput --settings=config.settings_collectstatic \
    && chown -R django:django /app

USER django
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health/live/', timeout=3)" || exit 1

CMD ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=3", "--timeout=60", "--access-logfile=-", "--error-logfile=-"]
