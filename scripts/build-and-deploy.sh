#!/bin/bash
# Build Lambda packages into build/ and deploy the CloudFormation stack.
# Requires: uv (https://github.com/astral-sh/uv), awscli
#
# Usage:
#   ./scripts/build-and-deploy.sh           # build + deploy
#   ./scripts/build-and-deploy.sh --build   # build only (no deploy)
#   ./scripts/build-and-deploy.sh --deploy  # deploy only (assumes build/ exists)
set -euo pipefail

STACK_NAME="asklore-stack"
ARTIFACTS_BUCKET="asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)"
BUILD_DIR="build"

BUILD=true
DEPLOY=true
if [[ "${1:-}" == "--build" ]];  then DEPLOY=false; fi
if [[ "${1:-}" == "--deploy" ]]; then BUILD=false;  fi

# ── Build ──────────────────────────────────────────────────────────────────────
if $BUILD; then
    echo "==> Cleaning ${BUILD_DIR}/"
    rm -rf "$BUILD_DIR"

    for lambda_src in lambda/*/; do
        name=$(basename "$lambda_src")
        dest="${BUILD_DIR}/${name}"
        mkdir -p "$dest"

        # Copy Python source files only
        cp "$lambda_src"*.py "$dest/" 2>/dev/null || true

        # Install dependencies alongside the handler using uv (fast, no resolver noise)
        if [[ -f "${lambda_src}requirements.txt" ]]; then
            echo "    [${name}] uv pip install..."
            uv pip install \
                -r "${lambda_src}requirements.txt" \
                --target "$dest" \
                --quiet \
                --python 3.12
        fi

        echo "    [${name}] built → ${dest}/"
    done
fi

# ── Package ────────────────────────────────────────────────────────────────────
echo "==> Packaging template..."
aws cloudformation package \
    --template-file template.yaml \
    --s3-bucket "$ARTIFACTS_BUCKET" \
    --output-template-file template-packaged.yaml

# ── Deploy ─────────────────────────────────────────────────────────────────────
if $DEPLOY; then
    echo "==> Deploying stack ${STACK_NAME}..."
    aws cloudformation deploy \
        --template-file template-packaged.yaml \
        --stack-name "$STACK_NAME" \
        --capabilities CAPABILITY_NAMED_IAM

    echo ""
    echo "==> Outputs:"
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs" --output table
fi
