#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only DevOps Agent access through the selected local profile.

These calls use the profile identity directly (no AssumeRole, no TagSession),
so status reads keep working when only the Chat path lacks permissions.
Nothing here creates, updates, or deletes DevOps Agent resources. The only
write is deleting an SQS message after its result was delivered.
"""
from __future__ import annotations

import datetime
import json
from typing import Any

import boto3
from botocore.config import Config

SDK_CONFIG = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2})
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELED", "CANCELLED", "SKIPPED"}


def _iso(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def _client(config: dict[str, Any], service: str):
    session = boto3.Session(profile_name=config["profile"], region_name=config["region"])
    return session.client(service, region_name=config["region"], config=SDK_CONFIG)


def list_investigations(config: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    client = _client(config, "devops-agent")
    response = client.list_backlog_tasks(
        agentSpaceId=config["agentSpaceId"],
        sortField="CREATED_AT",
        order="DESC",
        limit=max(1, min(limit, 20)),
    )
    return [
        {
            "taskId": task["taskId"],
            "title": task.get("title", ""),
            "status": task.get("status", ""),
            "priority": task.get("priority", ""),
            "taskType": task.get("taskType", ""),
            "createdAt": _iso(task.get("createdAt", "")),
        }
        for task in response.get("tasks", [])
    ]


def _summary_text(content: Any) -> str:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return content
    if isinstance(content, dict):
        for key in ("markdown", "text", "content", "summary"):
            if isinstance(content.get(key), str):
                return content[key]
    return json.dumps(content, ensure_ascii=False, default=str)


def get_investigation(
    config: dict[str, Any], task_id: str, summary_record_id: str | None = None
) -> dict[str, Any]:
    client = _client(config, "devops-agent")
    task = client.get_backlog_task(
        agentSpaceId=config["agentSpaceId"], taskId=task_id
    )["task"]
    execution_id = task.get("executionId")
    summary_record = None
    if execution_id and task.get("status") == "COMPLETED":
        paginator = client.get_paginator("list_journal_records")
        for page in paginator.paginate(
            agentSpaceId=config["agentSpaceId"],
            executionId=execution_id,
            recordType="investigation_summary_md",
            order="DESC",
        ):
            for record in page.get("records", []):
                if summary_record_id and record.get("recordId") != summary_record_id:
                    continue
                summary_record = record
                break
            if summary_record:
                break
    return {
        "taskId": task["taskId"],
        "executionId": execution_id,
        "title": task.get("title", ""),
        "status": task.get("status", ""),
        "priority": task.get("priority", ""),
        "taskType": task.get("taskType", ""),
        "updatedAt": _iso(task.get("updatedAt", "")),
        "summaryRecordId": summary_record.get("recordId") if summary_record else None,
        "summary": _summary_text(summary_record.get("content")) if summary_record else "",
        "summarySource": "investigation_summary_md" if summary_record else None,
    }


def get_alarm(config: dict[str, Any], alarm_name: str) -> dict[str, Any]:
    cloudwatch = _client(config, "cloudwatch")
    alarms = cloudwatch.describe_alarms(AlarmNames=[alarm_name]).get("MetricAlarms", [])
    if not alarms:
        return {"alarmName": alarm_name, "deployed": False}
    alarm = alarms[0]
    return {
        "alarmName": alarm_name,
        "deployed": True,
        "state": alarm.get("StateValue"),
        "reason": alarm.get("StateReason", ""),
        "updatedAt": _iso(alarm.get("StateUpdatedTimestamp", "")),
    }


def _last_log(config: dict[str, Any], group: str, pattern: str, minutes: int) -> dict[str, Any] | None:
    logs = _client(config, "logs")
    start = int((datetime.datetime.now().timestamp() - minutes * 60) * 1000)
    events = logs.filter_log_events(
        logGroupName=group, startTime=start, filterPattern=pattern, limit=50
    ).get("events", [])
    if not events:
        return None
    event = events[-1]
    return {
        "time": datetime.datetime.fromtimestamp(event["timestamp"] / 1000).astimezone().isoformat(),
        "message": event["message"].strip()[:300],
    }


def get_pipeline(config: dict[str, Any], demo_name: str, queue_url: str | None) -> dict[str, Any]:
    """Read-only snapshot of every stage of the demo flow."""
    ssm = _client(config, "ssm")
    try:
        fault = ssm.get_parameter(Name=f"/{demo_name}/fault-mode")["Parameter"]
        fault_state = {"value": fault["Value"], "updatedAt": _iso(fault.get("LastModifiedDate", ""))}
    except ssm.exceptions.ParameterNotFound:
        fault_state = {"value": None}

    workload = _last_log(config, f"/aws/lambda/{demo_name}-inventory-sync", '"inventory-sync"', 5)
    workload_level = None
    if workload:
        workload_level = "ERROR" if '"ERROR"' in workload["message"] else "INFO"

    trigger = _last_log(config, f"/aws/lambda/{demo_name}-trigger", '"taskId"', 60)
    trigger_task = None
    if trigger:
        try:
            trigger_task = json.loads(trigger["message"]).get("taskId")
        except ValueError:
            pass

    queue = None
    if queue_url:
        attrs = _client(config, "sqs").get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        queue = {
            "visible": int(attrs["ApproximateNumberOfMessages"]),
            "inFlight": int(attrs["ApproximateNumberOfMessagesNotVisible"]),
        }

    return {
        "fault": fault_state,
        "workload": {"level": workload_level, **(workload or {})},
        "trigger": {"taskId": trigger_task, **(trigger or {})},
        "queue": queue,
    }


def find_result_queue(config: dict[str, Any], stack_name: str) -> str | None:
    cfn = _client(config, "cloudformation")
    try:
        outputs = cfn.describe_stacks(StackName=stack_name)["Stacks"][0].get("Outputs", [])
    except Exception:  # stack absent or not readable: page still works without queue stage
        return None
    for output in outputs:
        if output["OutputKey"] == "ResultQueueUrl":
            return output["OutputValue"]
    return None


def poll_results(
    config: dict[str, Any], queue_url: str, max_messages: int = 5, ack: bool = False
) -> list[dict[str, Any]]:
    """Pull terminal investigation events from SQS over outbound HTTPS.

    A message is deleted only when ack=True and it was fully processed.
    Malformed or foreign messages are reported and left for the DLQ.
    """
    sqs = _client(config, "sqs")
    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=max(1, min(max_messages, 10)),
        WaitTimeSeconds=5,
        VisibilityTimeout=60,
    )
    results = []
    for message in response.get("Messages", []):
        item: dict[str, Any] = {"messageId": message["MessageId"], "valid": False}
        try:
            event = json.loads(message["Body"])
            metadata = event.get("detail", {}).get("metadata", {})
            data = event.get("detail", {}).get("data", {})
            if event.get("source") != "aws.aidevops":
                raise ValueError("source 不是 aws.aidevops")
            if metadata.get("agent_space_id") != config["agentSpaceId"]:
                raise ValueError("Agent Space ID 与本机 Profile 发现结果不一致")
            if event.get("account") != config["account_id"]:
                raise ValueError("事件账号与本机 Profile 账号不一致")
            task_id = metadata["task_id"]
            result = get_investigation(config, task_id, data.get("summary_record_id"))
            item.update({
                "valid": True,
                "eventType": event.get("detail-type"),
                "eventTime": event.get("time"),
                **result,
            })
            if ack:
                sqs.delete_message(
                    QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"]
                )
                item["acknowledged"] = True
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            item["error"] = str(exc)
        results.append(item)
    return results
