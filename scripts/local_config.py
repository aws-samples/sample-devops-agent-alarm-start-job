#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Discover DevOps Agent resources through a local AWS named profile.

No credentials are read or persisted by this module. boto3 uses the normal
credential chain configured by the user (including SSO/credential_process).
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound

PROFILE_RE = re.compile(r"^[A-Za-z0-9_+=,.@-]+$")
REGION_RE = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d+$")
SDK_CONFIG = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2})
ROLE_KINDS = ("iam", "idc", "idp")


def discover_local_config(
    profile: str,
    region: str,
    space_id: str | None = None,
    role_kind: str | None = None,
) -> dict[str, Any]:
    """Resolve account, Agent Space, and Operator App role from a local profile."""
    if not profile or not PROFILE_RE.fullmatch(profile):
        raise ValueError("必须提供有效的本机命名 Profile")
    if not REGION_RE.fullmatch(region):
        raise ValueError("区域格式无效")
    if role_kind and role_kind not in ROLE_KINDS:
        raise ValueError(f"role_kind 必须是：{', '.join(ROLE_KINDS)}")

    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        sts = session.client("sts", region_name=region, config=SDK_CONFIG)
        identity = sts.get_caller_identity()
        client = session.client("devops-agent", region_name=region, config=SDK_CONFIG)

        spaces = client.list_agent_spaces(maxResults=50).get("agentSpaces", [])
        if space_id:
            matches = [item for item in spaces if item.get("agentSpaceId") == space_id]
            if not matches:
                raise RuntimeError(
                    f"Profile {profile} 在 {region} 中看不到指定 Agent Space：{space_id}"
                )
            selected = matches[0]
        elif len(spaces) == 1:
            selected = spaces[0]
        elif not spaces:
            raise RuntimeError(
                f"Profile {profile} 在 {region} 中未发现 DevOps Agent Space"
            )
        else:
            choices = ", ".join(
                f"{item.get('name', '未命名')}={item.get('agentSpaceId')}" for item in spaces
            )
            raise RuntimeError(
                "发现多个 Agent Space，请用 --space-id 明确选择：" + choices
            )

        selected_space_id = selected["agentSpaceId"]
        operator = client.get_operator_app(agentSpaceId=selected_space_id)
        available_roles = {
            kind: operator[kind].get("operatorAppRoleArn")
            for kind in ROLE_KINDS
            if operator.get(kind) and operator[kind].get("operatorAppRoleArn")
        }
        if role_kind:
            role_arn = available_roles.get(role_kind)
            if not role_arn:
                raise RuntimeError(
                    f"Agent Space {selected_space_id} 未配置 {role_kind} Operator App"
                )
            selected_role_kind = role_kind
        elif len(available_roles) == 1:
            selected_role_kind, role_arn = next(iter(available_roles.items()))
        elif not available_roles:
            raise RuntimeError(
                f"Agent Space {selected_space_id} 未发现可用 Operator App Role"
            )
        else:
            choices = ", ".join(available_roles)
            raise RuntimeError(
                "发现多种 Operator App，请用 --role-kind 明确选择：" + choices
            )

        account_id = identity["Account"]
        role_account = role_arn.split(":")[4]
        if role_account != account_id:
            raise RuntimeError(
                f"身份账号 {account_id} 与 Operator Role 账号 {role_account} 不一致，已停止调用"
            )

        return {
            "source": "local_profile",
            "profile": profile,
            "account_id": account_id,
            "caller_arn": identity["Arn"],
            "region": region,
            "agentSpaceId": selected_space_id,
            "agentSpaceName": selected.get("name", ""),
            "operatorRoleArn": role_arn,
            "operatorRoleKind": selected_role_kind,
        }
    except ProfileNotFound as exc:
        raise RuntimeError(f"本机不存在 Profile：{profile}") from exc
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"通过本机 Profile {profile} 发现配置失败：{exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="从本机 Profile 发现 DevOps Agent 配置")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--space-id")
    parser.add_argument("--role-kind", choices=ROLE_KINDS)
    args = parser.parse_args()
    try:
        config = discover_local_config(
            args.profile, args.region, args.space_id, args.role_kind
        )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    account_id = str(config.get("account_id", ""))
    masked = f"****{account_id[-4:]}" if account_id else ""
    shown = {
        key: (value.replace(account_id, masked) if account_id and isinstance(value, str) else value)
        for key, value in config.items()
    }
    print(json.dumps(shown, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
