#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KiroCrew bridge for the Amazon DevOps Agent demo.

The bridge discovers resources from a local named profile and stores only
non-secret orchestration state (execution ID and resolved resource IDs).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from agent_api import chat as agent
from local_config import ROLE_KINDS, discover_local_config
from devops_reader import get_investigation, list_investigations, poll_results

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".kiro" / "runtime"
STATE_PATH = STATE_DIR / "devops-agent-state.json"


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(STATE_PATH)


def discover(args: argparse.Namespace) -> dict[str, Any]:
    return discover_local_config(
        args.profile, args.region, args.space_id, args.role_kind
    )


def _agent_args(config: dict[str, Any]) -> dict[str, Any]:
    """Common arguments for the in-process DevOps Agent calls (values from local discovery)."""
    return {
        "role_arn": config["operatorRoleArn"],
        "space_id": config["agentSpaceId"],
        "region": config["region"],
        "profile": config["profile"],
    }


def session_matches(state: dict[str, Any], config: dict[str, Any]) -> bool:
    return all(
        state.get(key) == config.get(key)
        for key in ("profile", "account_id", "region", "agentSpaceId", "operatorRoleArn")
    )


def ensure_chat(config: dict[str, Any], reset: bool = False) -> tuple[str, bool]:
    state = load_state()
    if not reset and session_matches(state, config) and state.get("executionId"):
        return state["executionId"], True

    payload = agent.create_chat(**_agent_args(config))
    execution_id = payload["executionId"]
    save_state({**config, "executionId": execution_id})
    return execution_id, False


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="KiroCrew × DevOps Agent 本地桥接器")
    parser.add_argument("--profile", required=True, help="本机 ~/.aws/config 中的命名 Profile")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--space-id")
    parser.add_argument("--role-kind", choices=ROLE_KINDS)
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("discover", help="发现并显示当前本机身份和 DevOps Agent 配置")

    chat = subparsers.add_parser("chat", help="复用同一 executionId 发送 Chat 问题")
    chat.add_argument("--question", required=True)
    chat.add_argument("--new-chat", action="store_true")

    investigation = subparsers.add_parser("investigate", help="提交异步 Investigation")
    investigation.add_argument("--title", required=True)
    investigation.add_argument("--description", required=True)
    investigation.add_argument("--priority", default="HIGH", choices=("HIGH", "MEDIUM", "LOW"))

    status = subparsers.add_parser("status", help="查询 Investigation 状态")
    status.add_argument("--task-id", required=True)

    summary = subparsers.add_parser("summary", help="读取已完成调查的正式 Journal 摘要")
    summary.add_argument("--task-id", required=True)

    latest = subparsers.add_parser("latest-completed", help="列出最近完成的 Investigation")
    latest.add_argument("--limit", type=int, default=5)

    poll = subparsers.add_parser("poll-results", help="从 SQS 出站拉取调查终态事件")
    poll.add_argument("--queue-url", required=True)
    poll.add_argument("--max-messages", type=int, default=5)
    poll.add_argument("--ack", action="store_true", help="处理成功后删除消息")

    args = parser.parse_args()
    try:
        config = discover(args)
        if args.action == "discover":
            emit(config)
            return
        if args.action == "chat":
            execution_id, reused = ensure_chat(config, reset=args.new_chat)
            a = _agent_args(config)
            answer = agent.send_message(
                a["role_arn"], a["space_id"], execution_id, args.question, a["region"],
                profile=a["profile"],
            ).strip()
            emit({
                "account": f"****{config['account_id'][-4:]}",
                "profile": config["profile"],
                "region": config["region"],
                "executionId": execution_id,
                "sessionReused": reused,
                "answer": answer,
            })
            return
        if args.action == "investigate":
            a = _agent_args(config)
            payload = agent.create_investigation(
                a["role_arn"], a["space_id"], args.title, args.description, args.priority,
                a["region"], profile=a["profile"],
            )
            emit({
                "account": f"****{config['account_id'][-4:]}",
                "profile": config["profile"],
                "region": config["region"],
                **payload,
            })
            return
        if args.action == "status":
            result = get_investigation(config, args.task_id)
            result.pop("summary", None)
            emit(result)
            return
        if args.action == "latest-completed":
            tasks = [
                task for task in list_investigations(config, limit=20)
                if task["status"] == "COMPLETED" and task["taskType"] == "INVESTIGATION"
            ]
            emit(tasks[: args.limit])
            return
        if args.action == "summary":
            result = get_investigation(config, args.task_id)
            if result.get("status") != "COMPLETED":
                raise RuntimeError(
                    f"调查尚未完成，当前状态：{result.get('status', 'UNKNOWN')}"
                )
            if not result.get("summary"):
                raise RuntimeError("调查已完成，但尚未找到 investigation_summary_md 记录")
            emit({
                "account": f"****{config['account_id'][-4:]}",
                "profile": config["profile"],
                "region": config["region"],
                **result,
            })
            return
        if args.action == "poll-results":
            emit({
                "account": f"****{config['account_id'][-4:]}",
                "profile": config["profile"],
                "region": config["region"],
                "results": poll_results(
                    config, args.queue_url, args.max_messages, ack=args.ack
                ),
            })
    except (ValueError, RuntimeError, BotoCoreError, ClientError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
