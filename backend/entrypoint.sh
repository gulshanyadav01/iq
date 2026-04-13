#!/bin/bash
set -e

if [ "$1" = "prod" ]; then
  echo "🔄 Running Django migrations..."
  python manage.py migrate --noinput

  echo "📦 Collecting static files..."
  python manage.py collectstatic --noinput

  echo "🚀 Starting Gunicorn..."
  exec gunicorn config.wsgi:application --bind 0.0.0.0:8000
elif [ "$1" = "dev" ]; then
    echo "🔄 Running Django migrations..."
    python manage.py migrate --noinput

    echo "🚀 Starting Django server..."
    exec python manage.py runserver 0.0.0.0:8000
elif [ "$1" = "celery" ]; then
  echo "📦 Starting Celery worker..."
  exec celery -A config worker --loglevel=INFO
elif [ "$1" = "celery-file-processing" ]; then
  echo "📁 Starting Celery file-processing worker (concurrency=10)..."
  exec celery -A config worker --queues=file_processing --concurrency=10 --loglevel=INFO --hostname=file-processing@%h
elif [ "$1" = "beat" ]; then
  echo "⏰ Starting Celery beat..."
  exec celery -A config beat --loglevel=INFO
else
  echo "❓ Unknown command: $1"
  exec "$@"
fi
