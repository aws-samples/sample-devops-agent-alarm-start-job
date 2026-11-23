#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Local status page for the KiroCrew and Amazon DevOps Agent demo.

The server binds to 127.0.0.1 only. It never receives or stores credentials;
boto3 resolves the selected local profile through the standard credential chain.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

from scripts import devops_reader as reader
from scripts.agent_api import chat as agent
from scripts.local_config import discover_local_config

ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "demo"
SCRIPT = ROOT / "scripts" / "devops-agent-chat.py"
PROFILE_RE = re.compile(r"^[A-Za-z0-9_+=,.@-]+$")
MAX_BODY_BYTES = 64 * 1024
DEMO_ALARM_NAME = "P2-DEMO-InventorySyncErrors"
DEMO_NAME = "devops-discovery-demo"
DEMO_STACK_NAME = "kirocrew-devops-discovery-demo"


def load_config(
    profile: str,
    region: str,
    space_id: str | None = None,
    role_kind: str | None = None,
) -> dict:
    return discover_local_config(profile, region, space_id, role_kind)


class UnsupportedMediaType(ValueError):
    """POST body was not sent as application/json."""


class DemoState:
    def __init__(self, config: dict, profile: str):
        self.config = config
        self.profile = profile
        self.execution_id: str | None = None
        self.result_queue_url: str | None = None
        self.lock = threading.Lock()

    def agent_args(self) -> dict:
        """Common arguments for the in-process DevOps Agent calls (values from local discovery)."""
        return {
            "role_arn": self.config["operatorRoleArn"],
            "space_id": self.config["agentSpaceId"],
            "region": self.config["region"],
            "profile": self.profile or None,
        }

    def ensure_chat(self, reset: bool = False) -> str:
        with self.lock:
            if reset:
                self.execution_id = None
            if self.execution_id:
                return self.execution_id
            payload = agent.create_chat(**self.agent_args())
            self.execution_id = payload["executionId"]
            return self.execution_id

    def chat(self, question: str) -> str:
        execution_id = self.ensure_chat()
        args = self.agent_args()
        return agent.send_message(
            args["role_arn"], args["space_id"], execution_id, question, args["region"],
            profile=args["profile"],
        ).strip()

    def create_investigation(self, title: str, description: str) -> dict:
        args = self.agent_args()
        return agent.create_investigation(
            args["role_arn"], args["space_id"], title, description, "HIGH", args["region"],
            profile=args["profile"],
        )

    def investigation_status(self, task_id: str) -> dict:
        result = reader.get_investigation(self.config, task_id)
        result.pop("summary", None)
        return result

    def investigation_summary(self, task_id: str) -> str:
        result = reader.get_investigation(self.config, task_id)
        if result.get("status") != "COMPLETED":
            raise RuntimeError(
                f"调查尚未完成，当前状态：{result.get('status', 'UNKNOWN')}"
            )
        summary = result.get("summary", "")
        if not summary:
            raise RuntimeError("调查已完成，但尚未找到 investigation_summary_md 记录")
        return summary

    def list_investigations(self, limit: int = 5) -> list[dict]:
        return reader.list_investigations(self.config, limit)


class Handler(SimpleHTTPRequestHandler):
    server_version = "DevOpsAgentDemo/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DEMO_DIR), **kwargs)

    @property
    def state(self) -> DemoState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def json_response(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def allowed_origins(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def request_rejected(self) -> bool:
        """Reject requests not addressed to this local server (DNS rebinding, cross-site POST).

        The Host header must name this server; a browser sends an Origin header on
        cross-origin and POST requests, and it must name this server as well.
        """
        allowed = self.allowed_origins()
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed:
            self.json_response({"ok": False, "error": "Host 不允许"}, HTTPStatus.FORBIDDEN)
            return True
        origin = self.headers.get("Origin")
        if origin is not None:
            parsed = urlparse(origin.strip().lower())
            if parsed.scheme != "http" or parsed.netloc not in allowed or parsed.path not in ("", "/"):
                self.json_response({"ok": False, "error": "Origin 不允许"}, HTTPStatus.FORBIDDEN)
                return True
        return False

    def read_json(self) -> dict:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise UnsupportedMediaType("Content-Type 必须是 application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求正文大小无效") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("请求正文大小无效")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求正文必须是 JSON 对象")
        return payload

    def do_GET(self) -> None:
        if self.request_rejected():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/config":
                config = self.state.config
                self.json_response(
                    {
                        "ok": True,
                        "configSource": config.get("source", "local_profile"),
                        "accountId": f"****{config['account_id'][-4:]}",
                        "region": config["region"],
                        "agentSpaceId": config["agentSpaceId"],
                        "agentSpaceName": config.get("agentSpaceName", ""),
                        "operatorRoleKind": config.get("operatorRoleKind", ""),
                        "profile": self.state.profile,
                    }
                )
                return
            if parsed.path == "/api/pipeline":
                state = self.state
                if state.result_queue_url is None:
                    state.result_queue_url = reader.find_result_queue(state.config, DEMO_STACK_NAME)
                data = reader.get_pipeline(state.config, DEMO_NAME, state.result_queue_url)
                data["alarm"] = reader.get_alarm(state.config, DEMO_ALARM_NAME)
                tasks = [t for t in reader.list_investigations(state.config, 10)
                         if t["taskType"] == "INVESTIGATION"]
                data["investigation"] = tasks[0] if tasks else None
                self.json_response({"ok": True, "data": data})
                return
            if parsed.path == "/api/alarm":
                self.json_response({
                    "ok": True,
                    "data": reader.get_alarm(self.state.config, DEMO_ALARM_NAME),
                })
                return
            if parsed.path == "/api/investigations":
                limit_text = parse_qs(parsed.query).get("limit", ["5"])[0]
                try:
                    limit = int(limit_text)
                except ValueError as exc:
                    raise ValueError("limit 必须是整数") from exc
                self.json_response({
                    "ok": True,
                    "data": self.state.list_investigations(limit),
                })
                return
            if parsed.path == "/api/investigation":
                task_id = parse_qs(parsed.query).get("task_id", [""])[0].strip()
                if not task_id:
                    raise ValueError("缺少 task_id")
                self.json_response({"ok": True, "data": self.state.investigation_status(task_id)})
                return
            super().do_GET()
        except (ValueError, RuntimeError, BotoCoreError, ClientError) as exc:
            self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        if self.request_rejected():
            return
        try:
            payload = self.read_json()
            if self.path == "/api/preflight":
                self.state.list_investigations(limit=1)
                self.json_response(
                    {
                        "ok": True,
                        "message": "本机身份、Operator Role、Agent Space 与只读任务查询链路均已通过。未创建 Chat 或 Investigation。",
                    }
                )
                return
            if self.path == "/api/chat":
                question = str(payload.get("question", "")).strip()
                if not question or len(question) > 4000:
                    raise ValueError("问题不能为空，且不能超过 4000 字符")
                self.json_response({"ok": True, "answer": self.state.chat(question)})
                return
            if self.path == "/api/investigation":
                title = str(payload.get("title", "")).strip()
                description = str(payload.get("description", "")).strip()
                if not title or not description:
                    raise ValueError("标题和调查描述不能为空")
                if len(title) > 200 or len(description) > 4000:
                    raise ValueError("标题或调查描述过长")
                self.json_response(
                    {"ok": True, "data": self.state.create_investigation(title, description)}
                )
                return
            if self.path == "/api/investigation/summary":
                task_id = str(payload.get("taskId", "")).strip()
                if not task_id:
                    raise ValueError("缺少 taskId")
                self.json_response(
                    {"ok": True, "answer": self.state.investigation_summary(task_id)}
                )
                return
            self.json_response({"ok": False, "error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except UnsupportedMediaType as exc:
            self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        except (ReadTimeoutError, ConnectTimeoutError):
            self.json_response(
                {"ok": False, "error": "调用超时。请保留真实 Task ID，并展示同一 Profile 下预先完成的调查。"},
                HTTPStatus.GATEWAY_TIMEOUT,
            )
        except (ValueError, RuntimeError, json.JSONDecodeError, BotoCoreError, ClientError) as exc:
            self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, state: DemoState):
        super().__init__(address, handler)
        self.state = state


def main() -> None:
    parser = argparse.ArgumentParser(description="KiroCrew × 亚马逊云科技 DevOps Agent 演示状态页")
    parser.add_argument("--profile", required=True, help="本机 ~/.aws/config 中的命名 Profile")
    parser.add_argument("--region", default="us-east-1", help="DevOps Agent 所在区域")
    parser.add_argument("--space-id", help="存在多个 Agent Space 时明确选择")
    parser.add_argument("--role-kind", choices=("iam", "idc", "idp"), help="存在多种 Operator App 时明确选择")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if not PROFILE_RE.fullmatch(args.profile):
        parser.error("Profile 名称包含不允许的字符")
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024-65535 之间")
    if not DEMO_DIR.is_dir() or not SCRIPT.is_file():
        parser.error("Demo 静态文件或 DevOps Agent 脚本不存在")

    try:
        config = load_config(args.profile, args.region, args.space_id, args.role_kind)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    state = DemoState(config, args.profile)
    server = DemoServer(("127.0.0.1", args.port), Handler, state)
    print("KiroCrew × 亚马逊云科技 DevOps Agent Demo（本机 Profile 实时模式）")
    print(f"Profile：{args.profile}")
    print(f"账号：****{config['account_id'][-4:]}｜区域：{config['region']}")
    print(f"Agent Space：{config['agentSpaceId']}")
    print(f"打开：http://127.0.0.1:{args.port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDemo 已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
