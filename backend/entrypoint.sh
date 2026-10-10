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
  if [ "$CELERY_AUTORELOAD" = "1" ]; then
    echo "🔁 Auto-reload enabled (watchmedo)"
    exec watchmedo auto-restart --directory=/app --pattern='*.py' --recursive -- \
      celery -A config worker --loglevel=INFO
  else
    exec celery -A config worker --loglevel=INFO
  fi
elif [ "$1" = "celery-policy-extract" ]; then
  # Recycle prefork children so long-running MinerU/Gemini tasks can't leak
  # memory across runs (whole PDFs + LLM payloads sit in RAM); prefetch=1 stops a
  # busy worker hoarding messages it can't start. Concurrency is the peak-memory
  # driver, so keep it modest and env-overridable (raise on a box with headroom).
  POLICY_EXTRACT_CONCURRENCY="${POLICY_EXTRACT_CONCURRENCY:-10}"
  PE_FLAGS="--queues=policy_extract --concurrency=${POLICY_EXTRACT_CONCURRENCY} --prefetch-multiplier=1 --max-tasks-per-child=50 --max-memory-per-child=512000 --loglevel=INFO --hostname=policy-extract@%h"
  echo "📄 Starting Celery policy-extract worker (concurrency=${POLICY_EXTRACT_CONCURRENCY})..."
  if [ "$CELERY_AUTORELOAD" = "1" ]; then
    echo "🔁 Auto-reload enabled (watchmedo)"
    exec watchmedo auto-restart --directory=/app --pattern='*.py' --recursive -- \
      celery -A config worker $PE_FLAGS
  else
    exec celery -A config worker $PE_FLAGS
  fi
elif [ "$1" = "celery-policy-extract-llm" ]; then
  # Gemini stages of the split policy_extract pipeline (POLICY_EXTRACT_SPLIT_LLM):
  # about 23% of a parse's wall time, I/O-bound on Gemini, so a few children suffice.
  POLICY_EXTRACT_LLM_CONCURRENCY="${POLICY_EXTRACT_LLM_CONCURRENCY:-4}"
  PEL_FLAGS="--queues=policy_extract_llm --concurrency=${POLICY_EXTRACT_LLM_CONCURRENCY} --prefetch-multiplier=1 --max-tasks-per-child=50 --max-memory-per-child=512000 --loglevel=INFO --hostname=policy-extract-llm@%h"
  echo "🧠 Starting Celery policy-extract LLM worker (concurrency=${POLICY_EXTRACT_LLM_CONCURRENCY})..."
  if [ "$CELERY_AUTORELOAD" = "1" ]; then
    echo "🔁 Auto-reload enabled (watchmedo)"
    exec watchmedo auto-restart --directory=/app --pattern='*.py' --recursive -- \
      celery -A config worker $PEL_FLAGS
  else
    exec celery -A config worker $PEL_FLAGS
  fi
elif [ "$1" = "celery-commission-intake" ]; then
  echo "📄 Starting Celery commission-intake worker (concurrency=2)..."
  if [ "$CELERY_AUTORELOAD" = "1" ]; then
    echo "🔁 Auto-reload enabled (watchmedo)"
    exec watchmedo auto-restart --directory=/app --pattern='*.py' --recursive -- \
      celery -A config worker --queues=commission_intake --concurrency=2 --loglevel=INFO --hostname=commission-intake@%h
  else
    exec celery -A config worker --queues=commission_intake --concurrency=2 --loglevel=INFO --hostname=commission-intake@%h
  fi
elif [ "$1" = "beat" ]; then
  echo "⏰ Starting Celery beat..."
  exec celery -A config beat --loglevel=INFO
else
  echo "❓ Unknown command: $1"
  exec "$@"
fi
