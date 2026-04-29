# InsurIQ — AWS EKS Deployment Guide

Complete step-by-step guide to deploy InsurIQ on AWS EKS with RDS PostgreSQL, ElastiCache Redis, and S3 storage.

**Target domain:** `insuriq.rhobots.ai`

---

## Prerequisites

You will need the following installed on your machine:

| Tool | Purpose | Install |
|---|---|---|
| AWS CLI | Talk to AWS | `brew install awscli` |
| eksctl | Create EKS cluster | `brew install eksctl` |
| kubectl | Manage Kubernetes | `brew install kubectl` |
| helm | Deploy the app | `brew install helm` |
| Docker | Build images | https://docs.docker.com/get-docker/ |

Verify all are installed:
```bash
aws --version
eksctl version
kubectl version --client
helm version
docker --version
```

---

## Step 1 — Configure AWS CLI

```bash
aws configure
```

Enter when prompted:
- **AWS Access Key ID** — from your AWS IAM user
- **AWS Secret Access Key** — from your AWS IAM user
- **Default region** — `ap-south-1`
- **Default output format** — `json`

Verify it works:
```bash
aws sts get-caller-identity
```

You should see your AWS account ID in the output.

---

## Step 2 — Create EKS Cluster

This creates a Kubernetes cluster on AWS with 2 worker nodes.

```bash
eksctl create cluster \
  --name insure-iq \
  --region ap-south-1 \
  --nodegroup-name workers \
  --node-type t3.medium \
  --nodes 2 \
  --nodes-min 2 \
  --nodes-max 4 \
  --managed
```

> This takes **15–20 minutes**. Go get a coffee.

Once done, verify the cluster is running:
```bash
kubectl get nodes
```

You should see 2 nodes with status `Ready`.

---

## Step 3 — Create RDS PostgreSQL Database

### 3a — Create a DB subnet group

```bash
# Get your VPC ID (created by eksctl)
VPC_ID=$(aws eks describe-cluster --name insure-iq --region ap-south-1 \
  --query "cluster.resourcesVpcConfig.vpcId" --output text)

echo "VPC ID: $VPC_ID"

# Get subnet IDs
aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query "Subnets[*].SubnetId" \
  --output text
```

Create a subnet group using the subnet IDs from above:
```bash
aws rds create-db-subnet-group \
  --db-subnet-group-name insure-iq-subnet-group \
  --db-subnet-group-description "InsurIQ DB subnet group" \
  --subnet-ids <subnet-id-1> <subnet-id-2>
```

### 3b — Create the RDS instance

```bash
aws rds create-db-instance \
  --db-instance-identifier insure-iq-postgres \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 17 \
  --master-username postgres \
  --master-user-password <YOUR_DB_PASSWORD> \
  --allocated-storage 20 \
  --db-name insur_iq \
  --db-subnet-group-name insure-iq-subnet-group \
  --no-publicly-accessible \
  --region ap-south-1
```

> Replace `<YOUR_DB_PASSWORD>` with a strong password. Save it — you'll need it later.

Wait for it to be available (~5 minutes):
```bash
aws rds wait db-instance-available \
  --db-instance-identifier insure-iq-postgres \
  --region ap-south-1
```

Get the DB hostname:
```bash
aws rds describe-db-instances \
  --db-instance-identifier insure-iq-postgres \
  --region ap-south-1 \
  --query "DBInstances[0].Endpoint.Address" \
  --output text
```

Save this hostname — it looks like:
`insure-iq-postgres.xxxxxxxxx.ap-south-1.rds.amazonaws.com`

### 3c — Create the auth database

The auth service needs its own database. Connect to RDS and create it:

```bash
# Port-forward via a temporary pod
kubectl run postgres-client --rm -it --image=postgres:17 -- \
  psql -h <RDS_HOSTNAME> -U postgres -c "CREATE DATABASE auth;"
```

---

## Step 4 — Create ElastiCache Redis

### 4a — Create subnet group

```bash
SUBNET_IDS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query "Subnets[*].SubnetId" \
  --output text | tr '\t' ',')

aws elasticache create-cache-subnet-group \
  --cache-subnet-group-name insure-iq-redis-subnet \
  --cache-subnet-group-description "InsurIQ Redis subnet" \
  --subnet-ids $SUBNET_IDS \
  --region ap-south-1
```

### 4b — Create Redis cluster

```bash
aws elasticache create-cache-cluster \
  --cache-cluster-id insure-iq-redis \
  --cache-node-type cache.t3.micro \
  --engine redis \
  --engine-version 7.0 \
  --num-cache-nodes 1 \
  --cache-subnet-group-name insure-iq-redis-subnet \
  --region ap-south-1
```

Wait for it (~5 minutes):
```bash
aws elasticache wait cache-cluster-available \
  --cache-cluster-id insure-iq-redis \
  --region ap-south-1
```

Get the Redis hostname:
```bash
aws elasticache describe-cache-clusters \
  --cache-cluster-id insure-iq-redis \
  --show-cache-node-info \
  --region ap-south-1 \
  --query "CacheClusters[0].CacheNodes[0].Endpoint.Address" \
  --output text
```

Save this hostname — it looks like:
`insure-iq-redis.xxxxxx.0001.aps1.cache.amazonaws.com`

---

## Step 5 — Create S3 Bucket

```bash
aws s3api create-bucket \
  --bucket insure-iq-storage \
  --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1

# Block public access (documents are private)
aws s3api put-public-access-block \
  --bucket insure-iq-storage \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

---

## Step 6 — Create ECR Repositories (Image Registry)

AWS ECR is where your Docker images are stored.

```bash
# Get your AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=ap-south-1

# Create repos
aws ecr create-repository --repository-name insure-iq-backend --region $REGION
aws ecr create-repository --repository-name insure-iq-web --region $REGION

echo "ECR registry: $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
```

---

## Step 7 — Build and Push Docker Images

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=ap-south-1
ECR_REGISTRY=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com

# Login to ECR
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $ECR_REGISTRY

# Build and push backend
docker build -f backend/Dockerfile -t insure-iq-backend:latest .
docker tag insure-iq-backend:latest $ECR_REGISTRY/insure-iq-backend:latest
docker push $ECR_REGISTRY/insure-iq-backend:latest

# Build and push web
docker build -f web/Dockerfile -t insure-iq-web:latest .
docker tag insure-iq-web:latest $ECR_REGISTRY/insure-iq-web:latest
docker push $ECR_REGISTRY/insure-iq-web:latest

echo "Images pushed to ECR successfully"
```

---

## Step 8 — Install nginx Ingress Controller

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/aws/deploy.yaml

# Wait for it to be ready
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=180s
```

Get the AWS Load Balancer hostname (you'll need this for DNS):
```bash
kubectl get svc -n ingress-nginx ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

Save this — it looks like:
`xxxxxxxx.ap-south-1.elb.amazonaws.com`

---

## Step 9 — Point DNS to Load Balancer

In your DNS provider (Route 53 or wherever `rhobots.ai` is managed):

1. Go to DNS settings for `rhobots.ai`
2. Add a **CNAME record**:
   - **Name:** `insuriq`
   - **Value:** `<load-balancer-hostname from Step 8>`
   - **TTL:** 300

After a few minutes, verify DNS is working:
```bash
nslookup insuriq.rhobots.ai
```

---

## Step 10 — Install cert-manager (HTTPS/TLS)

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.yaml

# Wait for it to be ready
kubectl wait --namespace cert-manager \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=180s
```

Create a Let's Encrypt cluster issuer:
```bash
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: tech@rhobots.ai
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
EOF
```

---

## Step 11 — Prepare Helm Values

### 11a — Create values.eks.yaml

Create a new file `helm/insure-iq/values.eks.yaml`:

```yaml
app:
  debug: "false"
  env: PROD
  csrfTrustedOrigins: "https://insuriq.rhobots.ai"
  hostName: "https://insuriq.rhobots.ai"

global:
  imageRegistry: "<ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/"

backend:
  replicas: 2

web:
  replicas: 2
  nuxtPublicAuthBaseUrl: "https://insuriq.rhobots.ai/auth/api/auth"
  nuxtPublicAppBaseUrl: "https://insuriq.rhobots.ai"
  nuxtPublicApiScheme: "https"
  nuxtPublicApiBaseUrl: "insuriq.rhobots.ai"

auth:
  baseDomain: "insuriq.rhobots.ai"
  trustedOrigins: "https://insuriq.rhobots.ai"

db:
  host: "<RDS_HOSTNAME>"     # from Step 3
  name: insur_iq
  user: postgres

postgres:
  enabled: false             # using RDS instead

redis:
  enabled: false             # using ElastiCache instead
  brokerUrl: "redis://<REDIS_HOSTNAME>:6379/0"
  resultBackend: "redis://<REDIS_HOSTNAME>:6379/1"
  cacheBackend: "redis://<REDIS_HOSTNAME>:6379/2"

minio:
  enabled: false             # using S3 instead
  s3EndpointUrl: "https://s3.ap-south-1.amazonaws.com"
  s3PublicEndpointUrl: "https://s3.ap-south-1.amazonaws.com"
  bucketName: insure-iq-storage
  region: ap-south-1

ingress:
  host: insuriq.rhobots.ai
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"

tls:
  enabled: true
  secretName: insuriq-tls
```

> Replace `<ACCOUNT_ID>`, `<RDS_HOSTNAME>`, `<REDIS_HOSTNAME>` with actual values from earlier steps.

### 11b — Create values.secrets.yaml

```bash
cp helm/insure-iq/values.secrets.yaml.example helm/insure-iq/values.secrets.yaml
```

Edit `helm/insure-iq/values.secrets.yaml` and fill in:

```yaml
secrets:
  # Django
  secretKey: "<run: openssl rand -hex 32>"
  webhookSecretKey: "<run: openssl rand -hex 32>"

  # Database
  dbPassword: "<YOUR_DB_PASSWORD from Step 3>"

  # Auth service
  betterAuthSecret: "<run: openssl rand -hex 32>"

  # AWS S3
  awsAccessKeyId: "<YOUR_AWS_ACCESS_KEY>"
  awsSecretAccessKey: "<YOUR_AWS_SECRET_KEY>"

  # OAuth (optional)
  googleClientId: ""
  googleClientSecret: ""
  githubClientId: ""
  githubClientSecret: ""
  microsoftClientId: ""
  microsoftClientSecret: ""

  # External APIs
  googleApiKey: "<YOUR_GOOGLE_API_KEY>"
  minervaApiUrl: "<MINERVA_API_URL>"
  policyExtractCallbackUrl: "https://insuriq.rhobots.ai/api/policy-extract/callback/"
```

Generate random secrets:
```bash
openssl rand -hex 32   # run 3 times for secretKey, webhookSecretKey, betterAuthSecret
```

---

## Step 12 — Deploy with Helm

```bash
helm install insure-iq ./helm/insure-iq \
  -f helm/insure-iq/values.eks.yaml \
  -f helm/insure-iq/values.secrets.yaml \
  -n insure-iq --create-namespace
```

---

## Step 13 — Verify Deployment

```bash
# Watch all pods come up (takes 3-5 minutes)
kubectl get pods -n insure-iq -w

# All pods should show 1/1 Running
```

Expected output:
```
NAME                                     READY   STATUS    RESTARTS
auth-xxx                                 1/1     Running   0
backend-xxx                              1/1     Running   0
celery-beat-xxx                          1/1     Running   0
celery-policy-extract-xxx                1/1     Running   0
celery-worker-xxx                        1/1     Running   0
web-xxx                                  1/1     Running   0
```

---

## Step 14 — Test the Application

```bash
# Backend health check
curl https://insuriq.rhobots.ai/api/health/
# Expected: {"status": "ok"}

# Auth service
curl https://insuriq.rhobots.ai/auth/api/auth/ok
# Expected: {"ok": true}

# Frontend
open https://insuriq.rhobots.ai
```

---

## Day-to-Day Operations

### Deploy a new version

```bash
# Build and push new images
docker build -f backend/Dockerfile -t $ECR_REGISTRY/insure-iq-backend:latest .
docker push $ECR_REGISTRY/insure-iq-backend:latest

# Restart pods to pull new image
kubectl rollout restart deployment/backend -n insure-iq
kubectl rollout status deployment/backend -n insure-iq
```

### Check logs

```bash
kubectl logs -n insure-iq deployment/backend --tail=100
kubectl logs -n insure-iq deployment/web --tail=100
kubectl logs -n insure-iq deployment/auth --tail=100
```

### Check pod status

```bash
kubectl get pods -n insure-iq
```

### Restart a service

```bash
kubectl rollout restart deployment/backend -n insure-iq
kubectl rollout restart deployment/web -n insure-iq
```

### Scale up/down

```bash
kubectl scale deployment/backend --replicas=3 -n insure-iq
```

---

## Troubleshooting

### Pod stuck in ImagePullBackOff
```bash
kubectl describe pod <pod-name> -n insure-iq
```
→ Check ECR login and image name in values.eks.yaml

### Pod stuck in CrashLoopBackOff
```bash
kubectl logs <pod-name> -n insure-iq --previous
```
→ Check environment variables and secrets

### Cannot reach the app
```bash
kubectl get ingress -n insure-iq
kubectl describe ingress -n insure-iq
```
→ Check DNS is pointing to the correct load balancer

### Database connection failed
```bash
kubectl logs deployment/backend -n insure-iq | grep -i "database\|postgres"
```
→ Check RDS security group allows traffic from EKS nodes

---

## Cleanup (when no longer needed)

```bash
# Delete Helm release
helm uninstall insure-iq -n insure-iq

# Delete EKS cluster (also deletes EC2 nodes)
eksctl delete cluster --name insure-iq --region ap-south-1

# Delete RDS (WARNING: deletes all data)
aws rds delete-db-instance \
  --db-instance-identifier insure-iq-postgres \
  --skip-final-snapshot

# Delete ElastiCache
aws elasticache delete-cache-cluster \
  --cache-cluster-id insure-iq-redis

# Delete S3 bucket
aws s3 rb s3://insure-iq-storage --force
```

---

## Cost Estimate (ap-south-1)

| Resource | Type | Approx. Monthly Cost |
|---|---|---|
| EKS Cluster | - | ~$70 |
| EC2 Nodes | 2x t3.medium | ~$60 |
| RDS PostgreSQL | db.t3.micro | ~$15 |
| ElastiCache Redis | cache.t3.micro | ~$12 |
| S3 Storage | per GB | ~$2 |
| Load Balancer | - | ~$16 |
| ECR | per GB | ~$1 |
| **Total** | | **~$176/month** |
