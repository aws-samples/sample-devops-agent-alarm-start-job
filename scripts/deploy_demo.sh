#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Deploy the proactive discovery demo. Run this yourself in a terminal.
# It creates a Change Set, shows every resource change, and executes only
# after you type "yes". Nothing here opens an inbound network endpoint.
set -euo pipefail

# Account-specific values come from .env.local (git-ignored, see .env.example) or the shell.
source "$(dirname "$0")/load_env.sh"
PROFILE="${PROFILE:?set PROFILE in .env.local}"
REGION="${REGION:-us-east-1}"
STACK="${STACK:-kirocrew-devops-discovery-demo}"
SPACE_ID="${SPACE_ID:?set SPACE_ID in .env.local (aws devops-agent list-agent-spaces)}"
LAYER_ARN="${LAYER_ARN:?set LAYER_ARN in .env.local (versioned boto3 layer ARN in your account)}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$ROOT/infrastructure/proactive-investigation-demo.yaml"
CHANGE_SET="demo-$(date +%Y%m%d%H%M%S)"
# --no-cli-pager: print the change set inline instead of opening `less`, so the yes/no prompt
# is reached without having to quit the pager first.
AWS=(aws --no-cli-pager --profile "$PROFILE" --region "$REGION")

echo "身份："
"${AWS[@]}" sts get-caller-identity --query Arn --output text
echo "Stack: ${STACK} | Region: ${REGION} | Agent Space: ${SPACE_ID}"

if "${AWS[@]}" cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  TYPE=UPDATE
else
  TYPE=CREATE
fi

"${AWS[@]}" cloudformation create-change-set \
  --stack-name "$STACK" \
  --change-set-name "$CHANGE_SET" \
  --change-set-type "$TYPE" \
  --template-body "file://$TEMPLATE" \
  --capabilities CAPABILITY_IAM \
  --tags Key=demo,Value=kirocrew-devops-discovery \
  --parameters \
    ParameterKey=AgentSpaceId,ParameterValue="$SPACE_ID" \
    ParameterKey=AgentSpaceRegion,ParameterValue="$REGION" \
    ParameterKey=Boto3LayerArn,ParameterValue="$LAYER_ARN" \
  >/dev/null

echo "等待 Change Set 生成…"
"${AWS[@]}" cloudformation wait change-set-create-complete \
  --stack-name "$STACK" --change-set-name "$CHANGE_SET"

"${AWS[@]}" cloudformation describe-change-set \
  --stack-name "$STACK" --change-set-name "$CHANGE_SET" \
  --query 'Changes[].ResourceChange.[Action,ResourceType,LogicalResourceId]' \
  --output table

read -r -p "确认以上变更并执行？输入 yes 继续：" answer
if [[ "$answer" != "yes" ]]; then
  "${AWS[@]}" cloudformation delete-change-set \
    --stack-name "$STACK" --change-set-name "$CHANGE_SET"
  echo "已取消，Change Set 已删除。"
  exit 0
fi

"${AWS[@]}" cloudformation execute-change-set \
  --stack-name "$STACK" --change-set-name "$CHANGE_SET"
echo "执行中，通常 2–4 分钟…"
if [[ "$TYPE" == CREATE ]]; then
  "${AWS[@]}" cloudformation wait stack-create-complete --stack-name "$STACK"
else
  "${AWS[@]}" cloudformation wait stack-update-complete --stack-name "$STACK"
fi

"${AWS[@]}" cloudformation describe-stacks --stack-name "$STACK" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
echo "部署完成。回到 KiroCrew 说“已部署”，继续做实际测试。"
