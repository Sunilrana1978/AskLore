#!/bin/bash
# Build Lambda packages into build/ and deploy the CloudFormation stack.
# Requires: uv (https://github.com/astral-sh/uv), awscli
#
# Usage:
#   ./scripts/build-and-deploy.sh           # build + deploy
#   ./scripts/build-and-deploy.sh --build   # build only (no deploy)
#   ./scripts/build-and-deploy.sh --deploy  # deploy only (assumes build/ exists)
set -euo pipefail

STACK_NAME="${STACK_NAME:-asklore-stack}"
ARTIFACTS_BUCKET="asklore-cfn-artifacts-$(aws sts get-caller-identity --query Account --output text)"
AOSS_ADMIN_PRINCIPAL_ARN="${AOSS_ADMIN_PRINCIPAL_ARN:-$(aws sts get-caller-identity --query Arn --output text)}"
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

        # Install dependencies alongside the handler using uv (fast, no resolver noise).
        # --python-platform/--only-binary cross-compile for Lambda's x86_64 Linux runtime
        # regardless of the host OS/arch this script runs on (e.g. macOS arm64) — without
        # this, packages with compiled extensions (e.g. pydantic_core, a google-genai dep)
        # install host-platform wheels that fail to import on Lambda.
        if [[ -f "${lambda_src}requirements.txt" ]]; then
            echo "    [${name}] uv pip install..."
            uv pip install \
                -r "${lambda_src}requirements.txt" \
                --target "$dest" \
                --quiet \
                --python 3.12 \
                --python-platform x86_64-unknown-linux-gnu \
                --only-binary :all:
        fi

        echo "    [${name}] built → ${dest}/"
    done
fi

# ── Validate ───────────────────────────────────────────────────────────────────
echo "==> Validating template..."
aws cloudformation validate-template --template-body file://template.yaml > /dev/null

# ── Package ────────────────────────────────────────────────────────────────────
echo "==> Packaging template..."
aws cloudformation package \
    --template-file template.yaml \
    --s3-bucket "$ARTIFACTS_BUCKET" \
    --output-template-file template-packaged.yaml

# ── Deploy ─────────────────────────────────────────────────────────────────────
if $DEPLOY; then
    ENV_NAME="${STACK_NAME#asklore-}"
    CONFIG_FILE="config/${ENV_NAME}.json"

    PARAM_OVERRIDES=("AossAdminPrincipalArn=${AOSS_ADMIN_PRINCIPAL_ARN}")
    if [[ -f "$CONFIG_FILE" ]]; then
        echo "==> Using ${CONFIG_FILE}"
        # aws cloudformation deploy's --parameter-overrides only accepts a
        # file:// reference as the sole value, not mixed with inline
        # Key=Value entries — so parse the JSON list here instead of
        # passing file://$CONFIG_FILE alongside AossAdminPrincipalArn.
        CONFIG_PARAMS=()
        while IFS= read -r param; do
            CONFIG_PARAMS+=("$param")
        done < <(python3 -c "import json,sys; print('\n'.join(json.load(open(sys.argv[1]))))" "$CONFIG_FILE")
        PARAM_OVERRIDES=("${CONFIG_PARAMS[@]}" "${PARAM_OVERRIDES[@]}")
    else
        echo "==> No ${CONFIG_FILE} found, using template defaults"
    fi

    echo "==> Deploying stack ${STACK_NAME}..."
    aws cloudformation deploy \
        --template-file template-packaged.yaml \
        --stack-name "$STACK_NAME" \
        --capabilities CAPABILITY_NAMED_IAM \
        --no-fail-on-empty-changeset \
        --parameter-overrides "${PARAM_OVERRIDES[@]}"

    echo ""
    echo "==> Outputs:"
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs" --output table
fi
