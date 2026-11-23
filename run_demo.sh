#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
source "$ROOT/scripts/load_env.sh"
PROFILE="${1:-${PROFILE:-}}"
REGION="${2:-${REGION:-us-east-1}}"
PORT="${3:-${PORT:-8765}}"

usage() {
  cat <<'EOF'
用法：
  bash run_demo.sh <本机Profile> [region] [port] [额外发现参数]
  bash run_demo.sh            # 不带参数时读取 .env.local 中的 PROFILE / REGION / PORT

示例：
  bash run_demo.sh my-devops-profile us-east-1 8765
  bash run_demo.sh my-devops-profile us-east-1 8765 --space-id <id>
  bash run_demo.sh my-devops-profile us-east-1 8765 --role-kind iam

说明：
  - Profile 必须存在于本机 ~/.aws/config。
  - 启动时实时发现账号、Agent Space 和 Operator Role。
  - 不读取项目中的样例账号配置。
EOF
}

if [[ -z "$PROFILE" || "$PROFILE" == "-h" || "$PROFILE" == "--help" ]]; then
  usage
  [[ -n "$PROFILE" ]] && exit 0 || exit 2
fi

[[ $# -gt 0 ]] && shift
[[ $# -gt 0 ]] && shift
[[ $# -gt 0 ]] && shift

# Pin the Agent Space from .env.local unless --space-id was passed explicitly, so a second
# Agent Space in the account does not make discovery ambiguous.
EXTRA=()
if [[ -n "${SPACE_ID:-}" && " $* " != *" --space-id "* ]]; then
  EXTRA=(--space-id "$SPACE_ID")
fi

printf '启动本机 Profile 实时 Demo：profile=%s，region=%s，port=%s\n' "$PROFILE" "$REGION" "$PORT"
exec python3 "$ROOT/demo_server.py" \
  --profile "$PROFILE" \
  --region "$REGION" \
  --port "$PORT" \
  ${EXTRA[@]+"${EXTRA[@]}"} \
  "$@"
