#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Read-only live view for the demo. Prints one status block every 15 seconds.
# Press Ctrl+C to stop. Nothing here changes any cloud resource.
set -uo pipefail

source "$(dirname "$0")/load_env.sh"
PROFILE="${PROFILE:?set PROFILE in .env.local}"
REGION="${REGION:-us-east-1}"
STACK="${STACK:-kirocrew-devops-discovery-demo}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AWS=(aws --no-cli-pager --profile "$PROFILE" --region "$REGION")

QUEUE_URL="$("${AWS[@]}" cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='ResultQueueUrl'].OutputValue" --output text)"

while true; do
  clear
  printf '=== 主动发现 Demo 实时状态 · %s ===\n\n' "$(date '+%H:%M:%S')"

  printf '故障开关    : %s\n' "$("${AWS[@]}" ssm get-parameter \
    --name /devops-discovery-demo/fault-mode --query Parameter.Value --output text)"

  printf '告警状态    : %s\n' "$("${AWS[@]}" cloudwatch describe-alarms \
    --alarm-names P2-DEMO-InventorySyncErrors \
    --query 'MetricAlarms[0].[StateValue,StateUpdatedTimestamp]' --output text)"

  printf '函数最新日志: %s\n' "$("${AWS[@]}" logs filter-log-events \
    --log-group-name /aws/lambda/devops-discovery-demo-inventory-sync \
    --start-time "$(( ($(date +%s) - 180) * 1000 ))" \
    --filter-pattern '"inventory-sync"' \
    --query 'events[-1].message' --output text | cut -c1-110)"

  printf '结果队列消息: %s\n\n' "$("${AWS[@]}" sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" --attribute-names ApproximateNumberOfMessages \
    --query Attributes.ApproximateNumberOfMessages --output text)"

  echo '最近的调查:'
  python3 - "$PROFILE" "$REGION" "$ROOT" "${SPACE_ID:-}" <<'PY'
import sys
sys.path.insert(0, sys.argv[3] + "/scripts")
from local_config import discover_local_config
from devops_reader import list_investigations
cfg = discover_local_config(sys.argv[1], sys.argv[2], sys.argv[4] or None)
for t in [x for x in list_investigations(cfg, 10) if x["taskType"] == "INVESTIGATION"][:3]:
    print(f"  {t['createdAt'][11:19]}  {t['status']:<12} {t['taskId'][:8]}  {t['title']}")
PY
  sleep 15
done
