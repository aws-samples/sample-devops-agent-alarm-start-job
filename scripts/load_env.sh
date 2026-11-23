#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Load KEY=VALUE pairs from <repo>/.env.local (git-ignored) into the environment.
# Variables already set in the shell win, so `SPACE_ID=... bash scripts/deploy_demo.sh` still works.
# The file is parsed, not executed: only simple KEY=VALUE lines are accepted.
_env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env.local"
if [[ -f "$_env_file" ]]; then
  while IFS= read -r _line || [[ -n "$_line" ]]; do
    [[ "$_line" =~ ^[[:space:]]*(#|$) ]] && continue
    if [[ "$_line" =~ ^[[:space:]]*([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
      _key="${BASH_REMATCH[1]}"
      _val="${BASH_REMATCH[2]%$'\r'}"
      _val="${_val#\"}"; _val="${_val%\"}"; _val="${_val#\'}"; _val="${_val%\'}"
      [[ -z "${!_key+x}" ]] && export "$_key=$_val"
    else
      echo "load_env.sh: ignoring malformed line in .env.local: $_line" >&2
    fi
  done < "$_env_file"
fi
unset _env_file _line _key _val
