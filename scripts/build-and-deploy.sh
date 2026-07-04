#!/bin/bash
# Install Lambda dependencies, package the CloudFormation template, and deploy.
set -euo pipefail

STACK_NAME="asklore-stack"
ARTIFACTS_BUCKET="asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)"

echo "==> Installing Lambda dependencies..."
for req in lambda/*/requirements.txt; do
    lambda_dir=$(dirname "$req")
    echo "    pip install for ${lambda_dir}"
    pip install -r "$req" -t "$lambda_dir" --quiet --upgrade
done

echo "==> Packaging template..."
aws cloudformation package \
    --template-file template.yaml \
    --s3-bucket "$ARTIFACTS_BUCKET" \
    --output-template-file template-packaged.yaml

echo "==> Deploying stack ${STACK_NAME}..."
aws cloudformation deploy \
    --template-file template-packaged.yaml \
    --stack-name "$STACK_NAME" \
    --capabilities CAPABILITY_NAMED_IAM

echo "==> Done. Outputs:"
aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs" --output table
