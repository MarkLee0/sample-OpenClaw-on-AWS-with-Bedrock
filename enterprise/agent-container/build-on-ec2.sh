#!/bin/bash
set -ex

# Get AWS account ID dynamically
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-openclaw-enterprise}"
S3_BUCKET="${S3_BUCKET:-openclaw-tenants-${ACCOUNT_ID}}"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${STACK_NAME}-multitenancy-agent"
LOG="/tmp/build.log"

exec > >(tee "$LOG") 2>&1

cd /tmp && rm -rf docker-build && mkdir docker-build && cd docker-build
aws s3 cp "s3://${S3_BUCKET}/_build/agent-build.tar.gz" . --region "$REGION"
tar xzf agent-build.tar.gz

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker build -f agent-container/Dockerfile -t "${ECR_URI}:latest" .
docker push "${ECR_URI}:latest"

echo "BUILD_AND_PUSH_COMPLETE"
aws s3 cp "$LOG" "s3://${S3_BUCKET}/_build/build.log" --region "$REGION"
