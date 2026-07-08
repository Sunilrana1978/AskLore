---
description: Validate and deploy the AskLore CloudFormation stack (make validate, then make build-deploy)
---

Run the AskLore deploy sequence in order, per CLAUDE.md's Deployment Rules:

1. `make validate` — validate `template.yaml` syntax first. If this fails, stop and fix the template before proceeding; do not attempt to deploy a stack that fails validation.
2. `make build-deploy` — rebuild `build/` from `lambda/*/` and deploy the stack.

Notes:
- Never run `--deploy` alone after editing a Lambda handler; `build/` will be stale. Always go through `make build-deploy`.
- To target a non-default stack, prefix with `STACK_NAME=asklore-<env>` (e.g. `STACK_NAME=asklore-dev make build-deploy`).
- `AOSS_ADMIN_PRINCIPAL_ARN` defaults to the current caller identity; override it for CI/CD roles.
- If the deploy fails partway (especially around `AossKbIndex` / `kb-index-setup`), check the "Known Deploy Gotchas" section of CLAUDE.md before retrying — several failure modes there (AOSS propagation delay, index visibility settle delay, orphaned log groups after rollback) are expected and have documented fixes, not new bugs.
- Report the final stack status and, on success, print stack outputs via:
  `aws cloudformation describe-stacks --stack-name <stack-name> --query "Stacks[0].Outputs" --output table`

If the user passed arguments (e.g. an environment name), use them to set `STACK_NAME` accordingly.
