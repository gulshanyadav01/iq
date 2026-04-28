# insure-iq Helm Chart

Production-ready Helm chart for deploying the insure-iq full-stack application (Django backend + Nuxt frontend) to Kubernetes.

## Quick Start

### 1. Prerequisites
- Helm 3.x
- kubectl configured to your cluster
- Docker images built: `insure-iq-backend:latest`, `insure-iq-web:latest`

### 2. Setup Secrets
```bash
cp values.secrets.yaml.example values.secrets.yaml
# Edit with your credentials
```

### 3. Deploy
```bash
# Development (in-cluster postgres/redis/minio)
./scripts/helm-quick-deploy.sh dev

# Production (external DB, higher replicas)
./scripts/helm-quick-deploy.sh prod
```

## Chart Structure

```
├── Chart.yaml                 # Chart metadata
├── values.yaml               # Shared defaults (no secrets, dev-safe)
├── values.dev.yaml           # Development overrides
├── values.prod.yaml          # Production overrides
├── values.secrets.yaml.example  # Secrets template (gitignored)
└── templates/
    ├── _helpers.tpl          # Template helpers
    ├── namespace.yaml        # Namespace creation
    ├── configmap.yaml        # App config (non-secret env vars)
    ├── secret.yaml           # Secrets (database passwords, API keys)
    ├── backend.yaml          # Django deployment + service
    ├── web.yaml              # Nuxt deployment + service
    ├── auth.yaml             # Better Auth deployment + service
    ├── celery-worker.yaml    # Celery worker deployment
    ├── celery-beat.yaml      # Celery beat scheduler
    ├── celery-policy-extract.yaml  # Policy extract worker
    ├── postgres.yaml         # PostgreSQL statefulset (conditional)
    ├── postgres-init-cm.yaml # Database init scripts (conditional)
    ├── redis.yaml            # Redis statefulset (conditional)
    ├── minio.yaml            # MinIO statefulset (conditional)
    ├── minio-init-job.yaml   # Bucket initialization job (conditional)
    └── ingress.yaml          # Ingress routing
```

## Values Structure

### Global Config
```yaml
global:
  namespace: insure-iq
  imageRegistry: ""           # e.g., "ghcr.io/rhobots-ai/" for prod
  imagePullPolicy: IfNotPresent
```

### Service Replicas
```yaml
backend:
  replicas: 1                # 2 in prod
web:
  replicas: 1                # 2 in prod
```

### Infrastructure Components
```yaml
postgres:
  enabled: true              # true in dev, false in prod
redis:
  enabled: true              # true in dev, false in prod
minio:
  enabled: true              # true in dev, false in prod
```

## Deployment Scenarios

### Development Environment
Uses in-cluster PostgreSQL, Redis, MinIO:
```bash
helm install insure-iq ./helm/insure-iq \
  -f values.dev.yaml \
  -f values.secrets.yaml \
  -n insure-iq --create-namespace
```

### Production Environment
Uses external managed databases (AWS RDS, ElastiCache, S3):
```bash
helm install insure-iq ./helm/insure-iq \
  -f values.prod.yaml \
  -f values.secrets.yaml \
  -n insure-iq --create-namespace
```

### Upgrade
```bash
helm upgrade insure-iq ./helm/insure-iq \
  -f values.dev.yaml \
  -f values.secrets.yaml
```

### Uninstall
```bash
helm uninstall insure-iq -n insure-iq
```

## Secrets Management

The chart uses two secret sources:

1. **values.secrets.yaml** (gitignored)
   - User-provided secrets at deployment time
   - Contains: database passwords, API keys, OAuth credentials

2. **Backend-generated secrets**
   - PostgreSQL/MinIO credentials generated in values.secrets.yaml
   - Passed to templates via `{{ .Values.secrets.* }}`

**Never commit real secrets to git.** Use environment variables or a secrets manager for production.

## Environments

### Development (values.dev.yaml)
```yaml
postgres:
  enabled: true
redis:
  enabled: true
minio:
  enabled: true
backend:
  replicas: 1
web:
  replicas: 1
```

### Production (values.prod.yaml)
```yaml
app:
  debug: "false"
  env: PROD
postgres:
  enabled: false              # Use external RDS
redis:
  enabled: false              # Use ElastiCache
minio:
  enabled: false              # Use AWS S3
backend:
  replicas: 2
web:
  replicas: 2
ingress:
  host: rhobots.ai            # Real domain
```

## Services and Networking

### Ingress Routes
| Path | Service | Port |
|------|---------|------|
| `/api` | backend | 80 → 8000 |
| `/auth` | auth | 80 → 10000 |
| `/` | web | 80 → 3000 |

### Internal Services (ClusterIP)
- `backend:80` → Django API
- `web:80` → Nuxt frontend
- `auth:80` → Better Auth service
- `postgres:5432` → PostgreSQL (if enabled)
- `redis:6379` → Redis (if enabled)
- `minio:9000` → MinIO S3 API (if enabled)

## Configuration

### Environment Variables
All non-secret config is in `configmap.yaml`, templated from `values.yaml`:
- `DEBUG`, `APP_ENV` — app mode
- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` — CORS/security
- `DB_HOST`, `DB_NAME`, `DB_USER` — database connection
- `CELERY_BROKER_URL`, `CELERY_*_BACKEND` — Redis queues
- `AWS_S3_ENDPOINT_URL`, `AWS_STORAGE_BUCKET_NAME` — S3/MinIO
- `NUXT_PUBLIC_*` — frontend config

### Secrets
All sensitive config is in `secret.yaml`, values provided via `values.secrets.yaml`:
- `SECRET_KEY`, `WEBHOOK_SECRET_KEY` — Django signing keys
- `DB_PASSWORD` — database password
- `BETTERAUTH_SECRET` — auth service secret
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — S3 credentials
- OAuth provider credentials (Google, GitHub, Microsoft)
- External API keys (`GOOGLE_API_KEY`, `MINERVA_API_URL`, etc.)

## Customization

### Custom Domain
Edit `values.yaml` or override at install time:
```bash
helm install insure-iq ./helm/insure-iq \
  -f values.prod.yaml \
  --set ingress.host=myapp.com
```

### Image Registry
For a custom registry (e.g., private ECR):
```bash
helm install insure-iq ./helm/insure-iq \
  --set global.imageRegistry=123456789.dkr.ecr.us-east-1.amazonaws.com/
```

### Resource Limits
```bash
helm install insure-iq ./helm/insure-iq \
  --set backend.resources.limits.memory=2Gi \
  --set backend.resources.limits.cpu=1000m
```

## Troubleshooting

### Pods not starting
```bash
kubectl get pods -n insure-iq
kubectl describe pod <pod-name> -n insure-iq
kubectl logs <pod-name> -n insure-iq
```

### Database migrations failed
```bash
# Check backend logs
kubectl logs -n insure-iq deployment/backend --previous
# Recreate to retry migrations
kubectl delete pod -n insure-iq -l app=backend
```

### MinIO bucket not created
```bash
# Check init job
kubectl get jobs -n insure-iq
kubectl logs -n insure-iq job/minio-init
```

### Secrets not mounted
```bash
# Verify secrets exist
kubectl get secrets -n insure-iq
kubectl describe secret backend-secrets -n insure-iq
```

## Production Checklist

- [ ] Copy `values.secrets.yaml.example` → `values.secrets.yaml`
- [ ] Fill in all credentials in `values.secrets.yaml`
- [ ] Set `ingress.host` to real domain
- [ ] Set `global.imageRegistry` to your container registry
- [ ] Build and push images: `insure-iq-backend`, `insure-iq-web`
- [ ] Configure external PostgreSQL and Redis (or use AWS RDS/ElastiCache)
- [ ] Configure S3 bucket for storage
- [ ] Update `values.prod.yaml` with correct endpoints/credentials
- [ ] Test with `helm template` before install
- [ ] Set up persistent volumes for statefulsets
- [ ] Configure ingress controller (nginx-ingress)
- [ ] Set up TLS certificates (cert-manager + letsencrypt)
- [ ] Monitor pods: `kubectl get pods -n insure-iq -w`

## Support

For issues:
1. Check logs: `kubectl logs -n insure-iq <pod>`
2. Check helm status: `helm status insure-iq -n insure-iq`
3. Validate chart: `helm lint ./helm/insure-iq`
4. Dry-run install: `helm install insure-iq ./helm/insure-iq --dry-run --debug`
