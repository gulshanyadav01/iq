# Kubernetes Migration Plan - InsurIQ

## Overview

This document outlines the complete migration from Docker Compose to Kubernetes with two environments:
- **Local/Development**: Using kind (Kubernetes-in-Docker)
- **Production**: Using EKS with managed services (RDS, ElastiCache, S3)

---

## 1. Architecture

### Local (kind)
```
┌────────────────────────────────────────────┐
│          kind cluster (local K8s)          │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │           Ingress (nginx)             │  │
│  │    / → web, /api → backend, /auth    │  │
│  └──────────────────────────────────────┘  │
│                     │                      │
│    ┌────────────────┼────────────────┐     │
│    ▼                ▼                ▼     │
│ ┌────────┐   ┌──────────┐   ┌───────┐     │
│ │  Web   │   │ Backend  │   │ Auth  │     │
│ │   pods │   │   pods   │   │ pods  │     │
│ └────────┘   └──────────┘   └───────┘     │
│                  │                         │
│    ┌─────────────┼─────────────┐            │
│    ▼             ▼             ▼           │
│ ┌────────┐ ┌────────┐ ┌──────────┐          │
│ │Postgres│ │ Redis  │ │  MinIO   │          │
│ │Stateful│ │Stateful│ │Stateful  │          │
│ │  Set   │ │  Set   │ │  Set    │          │
│ └────────┘ └────────┘ └──────────┘          │
└────────────────────────────────────────────┘
```

### Production (EKS)
```
┌────────────────────────────────────────┐
│       AWS Cloud / EKS Cluster          │
│                                         │
│  ┌────────────────────────────────┐    │
│  │   Ingress (nginx + AWS ALB)       │    │
│  └────────────────────────────────┘    │
│              │                           │
│    ┌─────────┼─────────┐               │
│    ▼         ▼         ▼              │
│ ┌──────┐ ┌───────┐ ┌─────┐        │
│ │ Web  │ │Backend│ │Auth │        │
│ └──────┘ └───────┘ └─────┘        │
│              │                    │
│    ┌─────────┼─────────┐         │
│    ▼         ▼         ▼         │
│  ┌──────┐ ┌───────┐           │
│  │ S3   │ │External│          │
│  │      │ │ (RDS/ │          │
│  │      │ │ElastiCache)        │
│  └──────┘ └───────┘          ��
└────────────────────────────────────────┘
```

---

## 2. Directory Structure

```
insur_iq/
├── k8s/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── namespace.yaml
│   │   ├── configmap.yaml
│   │   └── secrets.yaml
│   │
│   ├── components/
│   │   ├── postgres/
│   │   │   ├── statefulset.yaml
│   │   │   └── kustomization.yaml
│   │   ├── redis/
│   │   │   ├── statefulset.yaml
│   │   │   └── kustomization.yaml
│   │   ├── minio/
│   │   │   ├── statefulset.yaml
│   │   │   ├── init-job.yaml
│   │   │   └── kustomization.yaml
│   │   ├── ingress/
│   │   │   ├── nginx.yaml
│   │   │   └── kustomization.yaml
│   │   └── external-db/
│   │       ├── postgres-secret.yaml
│   │       ├── redis-secret.yaml
│   │       └── kustomization.yaml
│   │
│   ├── services/
│   │   ├── backend/
│   │   │   ├── deployment.yaml
│   │   │   └── kustomization.yaml
│   │   ├── web/
│   │   │   ├── deployment.yaml
│   │   │   └── kustomization.yaml
│   │   ├── auth/
│   │   │   ├── deployment.yaml
│   │   │   └── kustomization.yaml
│   │   └── celery/
│   │       ├── deployment.yaml
│   │       ├── beat-deployment.yaml
│   │       ├── cronjob-policy-extract.yaml
│   │       └── kustomization.yaml
│   │
│   ├── overlays/
│   │   ├── dev/
│   │   │   └── kustomization.yaml
│   │   └── prod/
│   │       └── kustomization.yaml
│   │
│   └── scripts/
│       ├── kind/
│       │   ├── kind-config.yaml
│       │   ├── create-cluster.sh
│       │   ├── install-ingress.sh
│       │   └── delete-cluster.sh
│       ├── deploy.sh
│       └── delete.sh
```

---

## 3. Prerequisites

### Install Required Tools

```bash
# kubectl (Kubernetes CLI)
brew install kubectl

# kind (Kubernetes-in-Docker)
brew install kind

# kustomize (included in kubectl)
kubectl version --client

# Docker Desktop or Docker CLI
brew install docker
```

---

## 4. Local Development (kind)

### Step 1: Create kind Cluster

```bash
./k8s/scripts/kind/create-cluster.sh
```

This creates a local K8s cluster with:
- 1 control-plane node
- Port mappings for HTTP traffic (80, 443, 3000, 8000, 10000)

### Step 2: Install nginx-ingress Controller

```bash
./k8s/scripts/kind/install-ingress.sh
```

### Step 3: Build Docker Images

Before deploying, build the application images:

```bash
# Backend
docker build -t insure-iq-backend:latest ./backend

# Web (from project root with Dockerfile)
docker build -t insure-iq-web:latest ./web
```

Or build using docker compose:
```bash
docker compose build backend web
docker tag insure-iq-backend:latest insure-iq-backend:latest
docker tag insure-iq-web:latest insure-iq-web:latest
```

### Step 4: Load Images into kind

```bash
kind load docker-image insure-iq-backend:latest --name insure-iq
kind load docker-image insure-iq-web:latest --name insure-iq
kind load docker-image rhobotsai/auth:latest --name insure-iq
```

### Step 5: Deploy to kind

```bash
kubectl apply -k k8s/overlays/dev
```

### Step 6: Verify Deployment

```bash
# Check pods
kubectl get pods -n insure-iq

# Check services
kubectl get svc -n insure-iq

# View logs
kubectl logs -n insure-iq -l app=backend -f
```

### Access Services

After deployment:
- Web: http://localhost:3000
- Backend API: http://localhost:8000/api/health/
- Auth: http://localhost:10000/api/auth/ok
- MinIO Console: http://localhost:9001

### Delete Local Cluster

```bash
./k8s/scripts/kind/delete-cluster.sh
```

---

## 5. Production (EKS)

### Step 1: Provision EKS Cluster

Using AWS CLI or Terraform:

```bash
# Create EKS cluster (example with AWS CLI)
aws eks create-cluster \
  --name insure-iq \
  --role-arn arn:aws:iam::ACCOUNT:role/eks-role \
  --resources-vpc-config subnetIds=subnet-xxx,subnet-yyy

# Update kubeconfig
aws eks update-kubeconfig --name insure-iq
```

### Step 2: Install nginx-ingress with AWS Load Balancer Controller

```bash
# Install AWS Load Balancer Controller
kubectl apply -k https://github.com/aws/load-balancer-controller/stack.yaml

# Install nginx-ingress
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.9.4.yaml \
  --set controller.service.annotations.service.beta.kubernetes.io/aws-load-balancer-type=nlb
```

### Step 3: Create RDS and ElastiCache

- Create RDS PostgreSQL instance
- Create ElastiCache Redis cluster
- Note the endpoints

### Step 4: Update External DB Configuration

Edit `k8s/components/external-db/` files with actual endpoints:

```yaml
# postgres-secret.yaml
stringData:
  username: prod-username
  password: prod-password

# redis-secret.yaml
stringData:
  redis-url: rediss://prod-xxx.cache.amazonaws.com:6379
```

Also update `k8s/overlays/prod/kustomization.yaml` with environment-specific values in configmap.

### Step 5: Build and Push Images

```bash
# Tag for ECR
ECR_REGISTRY=ACCOUNT.dkr.ecr.REGION.amazonaws.com

docker build -t $ECR_REGISTRY/backend:latest ./backend
docker build -t $ECR_REGISTRY/web:latest ./web

docker push $ECR_REGISTRY/backend:latest
docker push $ECR_REGISTRY/web:latest
```

### Step 6: Deploy to Production

```bash
kubectl apply -k k8s/overlays/prod
```

### Step 7: Configure DNS

Create Route53 record pointing to ingress NLB hostname.

---

## 6. Configuration

### ConfigMap Values

Edit `k8s/base/configmap.yaml` for base configuration:

| Variable | Description |
|----------|-------------|
| DEBUG | true/false |
| APP_ENV | DEV/PROD |
| DB_HOST | Database host |
| DB_NAME | Database name |
| CELERY_BROKER_URL | Redis broker URL |
| AWS_S3_ENDPOINT_URL | MinIO/S3 endpoint |
| NUXT_PUBLIC_API_BASE_URL | API URL for web |

### Secrets

Edit `k8s/base/secrets.yaml` for base secrets:

| Secret | Description |
|--------|-------------|
| SECRET_KEY | Django secret key |
| WEBHOOK_SECRET_KEY | Auth webhook key |
| DB_PASSWORD | Database password |
| AWS_ACCESS_KEY_ID | S3 access key |
| AWS_SECRET_ACCESS_KEY | S3 secret key |

### Environment-Specific Overlays

Dev overlay (`k8s/overlays/dev/`):
- Uses local StatefulSets for postgres, redis, minio
- Debug enabled
- Single replicas

Prod overlay (`k8s/overlays/prod/`):
- Uses external RDS, ElastiCache
- Debug disabled
- 2+ replicas

---

## 7. Common Commands

### Deploy
```bash
# Local
kubectl apply -k k8s/overlays/dev

# Production
kubectl apply -k k8s/overlays/prod
```

### Delete
```bash
# Local
kubectl delete -k k8s/overlays/dev

# Production
kubectl delete -k k8s/overlays/prod
```

### View Logs
```bash
kubectl logs -n insure-iq -l app=backend -f
kubectl logs -n insure-iq -l app=web -f
kubectl logs -n insure-iq -l app=auth -f
```

### Scale
```bash
kubectl scale deployment backend -n insure-iq --replicas=3
kubectl scale deployment web -n insure-iq --replicas=3
```

### Port Forward (local access)
```bash
kubectl port-forward -n insure-iq svc/backend 8000:80
kubectl port-forward -n insure-iq svc/web 3000:80
```

---

## 8. CI/CD

### GitHub Actions Example

```yaml
name: Deploy to EKS

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1

      - name: Login to ECR
        run: aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $ECR_REGISTRY

      - name: Build and push images
        run: |
          docker build -t backend:${{ github.sha }} ./backend
          docker push $ECR_REGISTRY/backend:${{ github.sha }}

      - name: Update image in kustomization
        run: |
          sed -i 's|insure-iq-backend:latest|$ECR_REGISTRY/backend:${{ github.sha }}|' k8s/services/backend/deployment.yaml

      - name: Deploy to EKS
        run: kubectl apply -k k8s/overlays/prod
        env:
          KUBECONFIG: ${{ secrets.KUBECONFIG }}
```

---

## 9. Troubleshooting

### Pod not starting
```bash
kubectl describe pod <pod-name> -n insure-iq
kubectl logs <pod-name> -n insure-iq
```

### Image pull errors
```bash
# Load image into kind
kind load docker-image <image> --name insure-iq
```

### Connection issues
```bash
# Check service endpoints
kubectl get endpoints -n insure-iq

# Debug DNS
kubectl run debug --image=busybox --rm -it -- nslookup <service-name>
```

### Reset everything
```bash
kubectl delete namespace insure-iq
kubectl apply -k k8s/overlays/dev
```

---

## 10. Files Created

- `k8s/base/` - Base namespace, configmap, secrets
- `k8s/components/postgres/` - PostgreSQL StatefulSet
- `k8s/components/redis/` - Redis StatefulSet
- `k8s/components/minio/` - MinIO StatefulSet + init job
- `k8s/components/ingress/` - nginx Ingress
- `k8s/components/external-db/` - External DB secrets (prod)
- `k8s/services/backend/` - Django Deployment + Service
- `k8s/services/web/` - Nuxt Deployment + Service
- `k8s/services/auth/` - Auth Deployment + Service
- `k8s/services/celery/` - Celery worker, beat, cronjob
- `k8s/overlays/dev/` - Local environment
- `k8s/overlays/prod/` - Production environment
- `k8s/scripts/kind/` - kind cluster scripts
- `k8s/scripts/deploy.sh` - Deployment script
- `k8s/scripts/delete.sh` - Deletion script