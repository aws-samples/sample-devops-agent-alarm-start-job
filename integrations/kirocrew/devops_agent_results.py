# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""KiroCrew cron script: pull DevOps Agent investigation results from SQS.

Register as a script cron, for example every 60 seconds, with ctx.message set to:
  {"profile": "<local profile>", "region": "<agent space region>",
   "queueUrl": "<ResultQueueUrl stack output>",
   "project": "<absolute path to this repository>",
   "spaceId": "<agent space id, required when the account has more than one>"}

The repository's own modules (scripts/local_config.py, scripts/devops_reader.py) are loaded
and called in-process; no child process is started. All network calls are outbound
(SQS long poll + DevOps Agent read APIs) using the local profile's standard credential
chain. The event payload is external data: it is validated by devops_reader and rendered
as text only, never executed. Quiet cycles produce no notification.
"""
import importlib.util
import json
import os

SUMMARY_LIMIT = 3500


def _load(project, name):
    path = os.path.join(project, "scripts", name + ".py")
    spec = importlib.util.spec_from_file_location("devops_demo_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _format(result, account, region):
    lines = [
        "主动发现：CloudWatch Alarm → Investigation",
        "账号：%s｜区域：%s" % (account, region),
        "事件：%s" % result.get("eventType", "-"),
        "调查：%s" % result.get("title", "-"),
        "Task ID：%s" % result.get("taskId", "-"),
        "状态：%s" % result.get("status", "-"),
    ]
    summary = result.get("summary") or ""
    if result.get("status") == "COMPLETED" and summary:
        lines.append("结果来源：investigation_summary_md")
        lines.append("")
        lines.append(summary[:SUMMARY_LIMIT] + ("…" if len(summary) > SUMMARY_LIMIT else ""))
    elif result.get("status") == "COMPLETED":
        lines.append("调查已完成，但未找到正式摘要记录，请在 DevOps Agent Web App 查看。")
    else:
        lines.append("调查未正常完成，不生成根因结论。")
    lines.append("")
    lines.append("所有缓解动作均需人工批准，本流程不修改任何资源。")
    return "\n".join(lines)


def run(ctx):
    config = json.loads(ctx.message)
    for key in ("profile", "region", "queueUrl", "project"):
        if not config.get(key):
            raise ValueError("ctx.message 缺少字段：%s" % key)
    if not os.path.isabs(config["project"]):
        raise ValueError("ctx.message.project 必须是仓库的绝对路径")
    project = os.path.realpath(config["project"])
    for name in ("local_config", "devops_reader"):
        if not os.path.isfile(os.path.join(project, "scripts", name + ".py")):
            raise ValueError("ctx.message.project 不是本仓库：缺少 scripts/%s.py" % name)

    local_config = _load(project, "local_config")
    reader = _load(project, "devops_reader")
    try:
        agent_config = local_config.discover_local_config(
            config["profile"], config["region"], config.get("spaceId") or None
        )
        results = reader.poll_results(agent_config, config["queueUrl"], 5, ack=True)
    except Exception as exc:  # report any discovery/SQS/API failure to the owner, then stop
        ctx.notify("DevOps Agent 结果拉取失败：\n%s" % str(exc)[:1500])
        return
    account = "****%s" % agent_config["account_id"][-4:]
    for result in results:
        if not result.get("valid"):
            ctx.notify(
                "收到无法核验的 DevOps Agent 事件，已保留在队列等待进入 DLQ：%s"
                % result.get("error", "未知原因")
            )
            continue
        ctx.notify(_format(result, account, config["region"]))
