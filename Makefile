.PHONY: build deploy build-deploy validate lint test help

STACK_NAME ?= asklore-stack

help:
	@echo "Usage:"
	@echo "  make build          Build Lambda packages into build/"
	@echo "  make deploy         Deploy existing build/ to CloudFormation"
	@echo "  make build-deploy   Build then deploy (default workflow)"
	@echo "  make validate       Validate template.yaml against CloudFormation"
	@echo "  make lint           Run ruff over all Lambda source files"
	@echo "  make test           Run unit tests"
	@echo ""
	@echo "Env vars:"
	@echo "  STACK_NAME=asklore-dev  Target stack (default: asklore-stack)"

build:
	bash scripts/build-and-deploy.sh --build

deploy:
	STACK_NAME=$(STACK_NAME) bash scripts/build-and-deploy.sh --deploy

build-deploy:
	STACK_NAME=$(STACK_NAME) bash scripts/build-and-deploy.sh

validate:
	aws cloudformation validate-template --template-body file://template.yaml

lint:
	uv run ruff check lambda/ scripts/

test:
	uv run pytest tests/ -v
