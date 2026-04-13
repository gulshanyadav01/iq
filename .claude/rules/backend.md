---
paths:
  - "backend/**"
---

# Backend Rules

- All settings via `django-environ` env vars — never hardcode values in settings.py
- New apps go in `backend/` alongside `accounts/`, `core/`, `system/`
- Register new apps in `INSTALLED_APPS` in `backend/config/settings.py`
- API views use Django REST Framework; serializers alongside views in the same app
- Async work goes through Celery tasks — never block request threads with long operations
- Scheduled tasks use Celery Beat (defined in `backend/config/celery.py`)
- File uploads use boto3/S3 storage (configured via `AWS_*` env vars)
- Run linting: `cd backend && pre-commit run --all-files`
- Migrations: `docker compose exec backend python manage.py makemigrations && migrate`
- OpenAPI schema: `docker compose exec backend python manage.py spectacular --file schema.yaml`
