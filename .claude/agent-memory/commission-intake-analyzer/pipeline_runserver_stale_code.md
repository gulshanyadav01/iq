# Stale runserver: code edits not reflected in UI/API on macOS Docker

The `backend` container runs `manage.py runserver` (StatReloader) with the
code bind-mounted (`./backend:/app`). On macOS Docker, inotify file-change
events do NOT reliably cross the bind mount, so the long-lived HTTP worker
keeps the code it loaded at container start. Edits on disk are real, but the
running server (and thus the frontend export/API) serves stale code.

**Tell:** `manage.py test` and `manage.py shell` spawn fresh processes and
DO see new code, so tests/shell verification pass while the UI shows old
behavior — exactly the trap that makes "the fix doesn't work" look like a
logic bug when it's a reload bug.

**Fix:** `docker compose restart backend celery_commission_intake celery_policy_extract`
after editing pipeline/view code. Celery workers have the same staleness.
Verify the UI/API, not just `manage.py shell`, after a fix.
