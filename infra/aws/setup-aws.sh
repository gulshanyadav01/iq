#!/usr/bin/env bash
#
# One-time AWS wiring for the GitHub Actions -> ECR push pipeline (keyless OIDC).
#
# Creates (all idempotent):
#   1. GitHub OIDC identity provider (token.actions.githubusercontent.com)
#   2. An IAM role GitHub Actions assumes, scoped to this repo's master/dev/tags
#   3. An inline ECR-push permissions policy on that role
#   4. Two ECR repositories (backend + web), MUTABLE tags, scan-on-push
#
# Then prints the role ARN and the exact `gh` commands to register the repo
# Variables + Secret that .github/workflows/ci.yml's push-ecr job reads.
#
# Requires: awscli v2 (configured with admin-ish creds for this one-time run), jq, gh.
#
# Usage:
#   ORG=rhobots-ai REPO=insur-iq AWS_REGION=ap-south-1 \
#   ECR_BACKEND_REPO=insur-iq/backend ECR_WEB_REPO=insur-iq/web \
#   ./infra/aws/setup-aws.sh
#
set -euo pipefail

: "${ORG:?Set ORG (GitHub org/user, e.g. rhobots-ai)}"
: "${REPO:?Set REPO (GitHub repo name, e.g. insur-iq)}"
: "${AWS_REGION:?Set AWS_REGION (e.g. ap-south-1)}"
: "${ECR_BACKEND_REPO:?Set ECR_BACKEND_REPO (e.g. insur-iq/backend)}"
: "${ECR_WEB_REPO:?Set ECR_WEB_REPO (e.g. insur-iq/web)}"

ROLE_NAME="${ROLE_NAME:-${REPO}-gha-ecr}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Preflight: confirm we have working AWS creds before doing anything.
if ! ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
  echo "ERROR: no working AWS credentials. Run this with admin creds for the target account." >&2
  exit 1
fi
OIDC_PROVIDER="token.actions.githubusercontent.com"
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_PROVIDER}"

echo "Account: ${ACCOUNT_ID} | Region: ${AWS_REGION} | Role: ${ROLE_NAME}"

# 1. OIDC provider (create only if absent)
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${OIDC_ARN}" >/dev/null 2>&1; then
  echo "OIDC provider exists."
else
  echo "Creating OIDC provider..."
  aws iam create-open-id-connect-provider \
    --url "https://${OIDC_PROVIDER}" \
    --client-id-list "sts.amazonaws.com" >/dev/null
fi

# 2. IAM role + trust policy (placeholders substituted from the shipped JSON)
TRUST_DOC="$(sed -e "s/ACCOUNT_ID/${ACCOUNT_ID}/g" -e "s#ORG/REPO#${ORG}/${REPO}#g" "${HERE}/oidc-trust-policy.json")"
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "Role exists — updating trust policy..."
  aws iam update-assume-role-policy --role-name "${ROLE_NAME}" --policy-document "${TRUST_DOC}" >/dev/null
else
  echo "Creating role..."
  aws iam create-role --role-name "${ROLE_NAME}" --assume-role-policy-document "${TRUST_DOC}" >/dev/null
fi

# 3. ECR-push permissions (inline policy)
PERM_DOC="$(sed -e "s/ACCOUNT_ID/${ACCOUNT_ID}/g" "${HERE}/ecr-push-policy.json")"
aws iam put-role-policy --role-name "${ROLE_NAME}" --policy-name "ecr-push" --policy-document "${PERM_DOC}" >/dev/null
echo "Attached ecr-push policy."

# 4. ECR repositories (MUTABLE tags + scan on push)
# MUTABLE is required because the pipeline pushes moving tags (latest, dev, master)
# that get overwritten on every release/branch push. IMMUTABLE would reject the
# second push of any of those tags.
for repo in "${ECR_BACKEND_REPO}" "${ECR_WEB_REPO}"; do
  if aws ecr describe-repositories --repository-names "${repo}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo "ECR repo ${repo} exists."
  else
    echo "Creating ECR repo ${repo}..."
    aws ecr create-repository \
      --repository-name "${repo}" \
      --region "${AWS_REGION}" \
      --image-tag-mutability MUTABLE \
      --image-scanning-configuration scanOnPush=true >/dev/null
  fi
done

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

cat <<EOF

------------------------------------------------------------------
Done. Role ARN:
  ${ROLE_ARN}

Now register the repo config the CI push-ecr job reads (run from the repo):

  gh variable set AWS_REGION       --body "${AWS_REGION}"
  gh variable set ECR_BACKEND_REPO --body "${ECR_BACKEND_REPO}"
  gh variable set ECR_WEB_REPO     --body "${ECR_WEB_REPO}"
  gh secret  set AWS_ROLE_ARN      --body "${ROLE_ARN}"

NOTE: AWS_ROLE_ARN is a SECRET; the three ECR_* / AWS_REGION are VARIABLES.
Putting the role ARN in Variables (or vice versa) makes the push job fail
with an opaque OIDC error.
------------------------------------------------------------------
EOF
