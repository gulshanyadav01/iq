# Deployment Guide

Complete guide to deploying insure-iq using Helm to Kubernetes.

## Quick Start (Recommended)

**All-in-one setup for local development:**

```bash
./scripts/setup-and-deploy.sh dev
```

This single command will:
- ✅ Create a local kind cluster
- ✅ Install nginx-ingress controller
- ✅ Create secrets file
- ✅ Deploy all services
- ✅ Show access instructions

---

## Step-by-Step Setup

### 1. Create a Kubernetes Cluster

**Option A: kind (recommended for local dev)**
```bash
./scripts/create-kind-cluster.sh
```

**Option B: minikube**
```bash
minikube start --cpus=4 --memory=8192
```

**Option C: EKS / GKE / AKS**
```bash
# Use your cloud provider's CLI
# Example for AWS EKS:
aws eks create-cluster --name insure-iq --region us-east-1
```

### 2. Install Ingress Controller

For kind clusters:
```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/kind/deploy.yaml

# Wait for it to be ready
kubectl wait --namespace ingress-nginx \
    --for=condition=ready pod \
    --selector=app.kubernetes.io/component=controller \
    --timeout=90s
```

For other clusters, follow: https://kubernetes.github.io/ingress-nginx/deploy/

### 3. Prepare Secrets

```bash
cp helm/insure-iq/values.secrets.yaml.example helm/insure-iq/values.secrets.yaml
```

Edit `helm/insure-iq/values.secrets.yaml` and fill in:
- `secretKey` — random Django secret (use `openssl rand -hex 32`)
- `webhookSecretKey` — random webhook secret
- `betterAuthSecret` — random auth service secret
- `dbPassword` — database password (for dev: can be simple)
- OAuth credentials (if using social login)
- External API keys

**For development**, you can use simple values:
```yaml
secrets:
  secretKey: "dev-secret-key-123"
  webhookSecretKey: "dev-webhook-secret-123"
  betterAuthSecret: "dev-auth-secret-123"
  dbPassword: "postgres"
  # ... rest can be empty or simple values
```

### 4. Deploy with Helm

**Quick deployment (dev):**
```bash
./scripts/helm-quick-deploy.sh dev
```

**Full control (interactive):**
```bash
./scripts/helm-deploy.sh
```

**Manual (production):**
```bash
helm install insure-iq ./helm/insure-iq \
  -f helm/insure-iq/values.prod.yaml \
  -f helm/insure-iq/values.secrets.yaml \
  -n insure-iq --create-namespace
```

---

## Deployment Scripts

| Script | Use Case |
|--------|----------|
| `setup-and-deploy.sh` | **Recommended**: One command to set up everything locally |
| `helm-quick-deploy.sh dev\|prod` | Quick deploy (assumes cluster exists) |
| `helm-deploy.sh` | Interactive deployment with step-by-step guidance |
| `create-kind-cluster.sh` | Just create a local kind cluster |

### Usage Examples

```bash
# Complete local setup (cluster + deploy)
./scripts/setup-and-deploy.sh dev

# Just deploy to existing cluster
./scripts/helm-quick-deploy.sh dev

# Interactive guided deployment
./scripts/helm-deploy.sh

# Create cluster only
./scripts/create-kind-cluster.sh
```

---

## Verification

After deployment, verify everything is running:

```bash
# Check all pods are running
kubectl get pods -n insure-iq

# Check deployment status
kubectl get deployment -n insure-iq

# View recent logs
kubectl logs -n insure-iq deployment/backend --tail=50
kubectl logs -n insure-iq deployment/web --tail=50

# Check Helm status
helm status insure-iq -n insure-iq

# Watch pods come up
kubectl get pods -n insure-iq -w
```

---

## Accessing the Application

### From Ingress (recommended)

If using a local kind cluster:
```bash
# Backend API
curl http://localhost/api/health/

# Auth service
curl http://localhost/auth/api/auth/ok

# Frontend
open http://localhost
```

### Via Port-Forward

```bash
# Terminal 1: Forward backend
kubectl port-forward -n insure-iq svc/backend 8000:80

# Terminal 2: Forward web
kubectl port-forward -n insure-iq svc/web 3000:80

# Terminal 3: Forward auth
kubectl port-forward -n insure-iq svc/auth 10000:80

# Access locally
curl http://localhost:8000/api/health/
open http://localhost:3000
```

---

## Troubleshooting

### Pods not starting

```bash
# Get detailed pod information
kubectl describe pod <pod-name> -n insure-iq

# View logs
kubectl logs <pod-name> -n insure-iq --previous  # If crashed

# Check events
kubectl get events -n insure-iq
```

### Database migration failed

```bash
# Check backend logs
kubectl logs -n insure-iq deployment/backend

# Restart to retry migrations
kubectl rollout restart deployment/backend -n insure-iq
```

### MinIO bucket not created (dev only)

```bash
# Check init job
kubectl get jobs -n insure-iq
kubectl logs -n insure-iq job/minio-init
kubectl describe job minio-init -n insure-iq

# Manually create bucket
kubectl port-forward -n insure-iq svc/minio 9000:9000
# In another terminal:
# mc alias set myminio http://localhost:9000 minio miniosecret
# mc mb myminio/insure-iq
# mc anonymous set download myminio/insure-iq
```

### Secrets not found

```bash
# Verify secrets exist
kubectl get secrets -n insure-iq
kubectl describe secret backend-secrets -n insure-iq

# If missing, check values.secrets.yaml exists and run upgrade
helm upgrade insure-iq ./helm/insure-iq \
  -f helm/insure-iq/values.dev.yaml \
  -f helm/insure-iq/values.secrets.yaml \
  -n insure-iq
```

### Ingress not routing traffic

```bash
# Check ingress
kubectl get ingress -n insure-iq
kubectl describe ingress -n insure-iq

# Check ingress controller status
kubectl get pods -n ingress-nginx

# Test from inside cluster
kubectl run -it --rm debug --image=busybox:1.28 -- sh
# Inside the pod:
# wget -q -O- http://backend/api/health/
# wget -q -O- http://web
```

---

## Upgrade

After making changes to the Helm chart or values:

```bash
# Dev
helm upgrade insure-iq ./helm/insure-iq \
  -f helm/insure-iq/values.dev.yaml \
  -f helm/insure-iq/values.secrets.yaml \
  -n insure-iq

# Prod
helm upgrade insure-iq ./helm/insure-iq \
  -f helm/insure-iq/values.prod.yaml \
  -f helm/insure-iq/values.secrets.yaml \
  -n insure-iq
```

## Rollback

If something breaks:

```bash
# See previous releases
helm history insure-iq -n insure-iq

# Rollback to previous version
helm rollback insure-iq 1 -n insure-iq  # 1 is the release revision
```

---

## Cleanup

### Delete the release
```bash
helm uninstall insure-iq -n insure-iq
```

### Delete the namespace (and everything in it)
```bash
kubectl delete namespace insure-iq
```

### Delete the cluster (kind only)
```bash
kind delete cluster --name insure-iq
```

---

## Production Checklist

- [ ] Cluster is set up on AWS/GCP/Azure
- [ ] Persistent volumes are configured for stateful services
- [ ] External PostgreSQL (RDS) is provisioned
- [ ] External Redis (ElastiCache) is provisioned
- [ ] S3 bucket is created and configured
- [ ] Docker images are pushed to a private registry
- [ ] values.secrets.yaml has real credentials (not template)
- [ ] ingress.host is set to real domain
- [ ] TLS certificates are set up (cert-manager + letsencrypt)
- [ ] Nginx ingress controller is installed
- [ ] Resource limits and requests are tuned
- [ ] Monitoring and logging are configured (Prometheus, ELK, etc.)
- [ ] Backups are configured for databases
- [ ] Deployment has been tested end-to-end

---

## Environment Variables Reference

All configuration is managed through Helm values. Key sections:

### App Configuration (values.yaml)
- `app.debug` — Django debug mode
- `app.env` — Environment name (DEV/PROD)
- `app.allowedHosts` — CORS allowed hosts

### Database (values.yaml + values.secrets.yaml)
- `db.host` — PostgreSQL hostname
- `db.name` — Database name
- `secrets.dbPassword` — Database password

### Celery/Redis
- `redis.brokerUrl` — Celery broker URL
- `redis.resultBackend` — Celery result backend

### Storage (MinIO/S3)
- `minio.s3EndpointUrl` — S3 endpoint
- `secrets.awsAccessKeyId` — S3 access key
- `secrets.awsSecretAccessKey` — S3 secret key

### External Services
- `secrets.googleApiKey` — Google API key
- `secrets.minervaApiUrl` — Minerva API URL
- OAuth credentials (Google, GitHub, Microsoft)

---

## Support

For issues:
1. Check pod logs: `kubectl logs -n insure-iq <pod>`
2. Check Helm release: `helm status insure-iq -n insure-iq`
3. Validate chart: `helm lint ./helm/insure-iq`
4. Test with dry-run: `helm install insure-iq ./helm/insure-iq --dry-run --debug`
5. Check values: `helm get values insure-iq -n insure-iq`
