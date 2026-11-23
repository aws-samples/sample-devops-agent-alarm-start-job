#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
AWS DevOps Agent CLI
用法:
  # Chat
  create-chat          --role-arn <arn> --space-id <id> [--region] [--user-id]
  send-message         --role-arn <arn> --space-id <id> --execution-id <id> --content <text> [--region] [--user-id]
  list-chats           --role-arn <arn> --space-id <id> [--max-results] [--region] [--user-id]
  list-journal-records --role-arn <arn> --space-id <id> --execution-id <id> [--record-type] [--limit] [--order ASC|DESC] [--region]

  # Investigation
  create-investigation  --role-arn <arn> --space-id <id> --title <title> --description <desc> [--priority HIGH|MEDIUM|LOW] [--region]
  get-investigation     --role-arn <arn> --space-id <id> --task-id <id> [--region]
  list-investigations   --role-arn <arn> --space-id <id> [--status] [--limit] [--region]
  list-executions       --role-arn <arn> --space-id <id> --task-id <id> [--limit] [--region]
  list-recommendations  --role-arn <arn> --space-id <id> [--task-id] [--status] [--priority] [--limit] [--region]

Every command function returns its result, so other modules can import and call them in-process.
The CLI prints the result. `profile` selects a named AWS profile; None uses the default chain.
"""
import argparse
import json
import sys

import boto3
from botocore.config import Config

BOTO_CONFIG = Config(
    connect_timeout=10,
    read_timeout=600,
    retries={"max_attempts": 0},
)


class AgentError(RuntimeError):
    """The DevOps Agent reported a failed response."""


def assume_role(role_arn, space_id, region, profile=None):
    session = boto3.Session(profile_name=profile, region_name=region)
    resp = session.client('sts', region_name=region).assume_role(
        RoleArn=role_arn,
        RoleSessionName="DevOpsAgentChat",
        Tags=[{"Key": "AgentSpaceId", "Value": space_id}]
    )
    c = resp['Credentials']
    return boto3.client(
        'devops-agent',
        region_name=region,
        aws_access_key_id=c['AccessKeyId'],
        aws_secret_access_key=c['SecretAccessKey'],
        aws_session_token=c['SessionToken'],
        config=BOTO_CONFIG,
    )


def json_default(obj):
    import datetime
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def jprint(obj):
    print(json.dumps(obj, default=json_default, ensure_ascii=False, indent=2))


# ── Chat ──────────────────────────────────────────────────────────────────────

def create_chat(role_arn, space_id, region, user_id='AWSDevopsAgents', profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    resp = client.create_chat(agentSpaceId=space_id, userId=user_id)
    return {"executionId": resp['executionId']}


def send_message(role_arn, space_id, execution_id, content, region, user_id='AWSDevopsAgents', profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    response = client.send_message(
        agentSpaceId=space_id,
        executionId=execution_id,
        content=content,
        userId=user_id
    )
    parts = []
    current_block_type = None
    for event in response['events']:
        if 'contentBlockStart' in event:
            current_block_type = event['contentBlockStart'].get('type')
        elif 'contentBlockDelta' in event:
            if current_block_type in (None, 'text'):
                delta = event['contentBlockDelta'].get('delta', {})
                if 'textDelta' in delta:
                    parts.append(delta['textDelta']['text'])
        elif 'contentBlockStop' in event:
            current_block_type = None
        elif 'responseFailed' in event:
            raise AgentError(event['responseFailed']['errorMessage'])
    return "".join(parts)


def list_chats(role_arn, space_id, max_results, region, user_id='AWSDevopsAgents', profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    kwargs = dict(agentSpaceId=space_id, userId=user_id)
    if max_results:
        kwargs['maxResults'] = max_results
    return client.list_chats(**kwargs).get('executions', [])


def list_journal_records(role_arn, space_id, execution_id, record_type, limit, order, region, profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    kwargs = dict(agentSpaceId=space_id, executionId=execution_id, order=order)
    if record_type:
        kwargs['recordType'] = record_type
    if limit:
        kwargs['limit'] = limit
    return client.list_journal_records(**kwargs).get('records', [])


# ── Investigation ─────────────────────────────────────────────────────────────

def create_investigation(role_arn, space_id, title, description, priority, region, profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    resp = client.create_backlog_task(
        agentSpaceId=space_id,
        taskType='INVESTIGATION',
        title=title,
        description=description,
        priority=priority,
    )
    task = resp['task']
    return {
        "taskId": task['taskId'],
        "executionId": task['executionId'],
        "status": task['status'],
    }


def get_investigation(role_arn, space_id, task_id, region, profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    task = client.get_backlog_task(agentSpaceId=space_id, taskId=task_id)['task']
    return {
        "taskId": task['taskId'],
        "executionId": task.get('executionId', ''),
        "status": task['status'],
        "title": task.get('title', ''),
        "priority": task.get('priority', ''),
        "updatedAt": task.get('updatedAt', ''),
    }


def list_investigations(role_arn, space_id, status, limit, region, profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    kwargs = dict(agentSpaceId=space_id, sortField='CREATED_AT', order='DESC')
    if status:
        kwargs['filter'] = {'status': [status]}
    if limit:
        kwargs['limit'] = limit
    tasks = client.list_backlog_tasks(**kwargs).get('tasks', [])
    return [{
        "taskId": t['taskId'],
        "title": t.get('title', ''),
        "status": t['status'],
        "priority": t.get('priority', ''),
        "createdAt": t.get('createdAt', ''),
    } for t in tasks]


def list_executions(role_arn, space_id, task_id, limit, region, profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    kwargs = dict(agentSpaceId=space_id, taskId=task_id)
    if limit:
        kwargs['limit'] = limit
    return client.list_executions(**kwargs).get('executions', [])


def list_recommendations(role_arn, space_id, task_id, status, priority, limit, region, profile=None):
    client = assume_role(role_arn, space_id, region, profile)
    kwargs = dict(agentSpaceId=space_id)
    if task_id:
        kwargs['taskId'] = task_id
    if status:
        kwargs['status'] = status
    if priority:
        kwargs['priority'] = priority
    if limit:
        kwargs['limit'] = limit
    recs = client.list_recommendations(**kwargs).get('recommendations', [])
    return [{
        "recommendationId": r['recommendationId'],
        "title": r.get('title', ''),
        "status": r.get('status', ''),
        "priority": r.get('priority', ''),
        "summary": r.get('content', {}).get('summary', ''),
    } for r in recs]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command')

    def common(p):
        p.add_argument('--role-arn', required=True)
        p.add_argument('--space-id', required=True)
        p.add_argument('--region', default='us-east-1')

    # create-chat
    p = sub.add_parser('create-chat')
    common(p)
    p.add_argument('--user-id', default='AWSDevopsAgents')

    # send-message
    p = sub.add_parser('send-message')
    common(p)
    p.add_argument('--execution-id', required=True)
    p.add_argument('--content', required=True)
    p.add_argument('--user-id', default='AWSDevopsAgents')

    # list-chats
    p = sub.add_parser('list-chats')
    common(p)
    p.add_argument('--max-results', type=int, default=None)
    p.add_argument('--user-id', default='AWSDevopsAgents')

    # list-journal-records
    p = sub.add_parser('list-journal-records')
    common(p)
    p.add_argument('--execution-id', required=True)
    p.add_argument('--record-type', default=None)
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--order', default='DESC', choices=['ASC', 'DESC'])

    # create-investigation
    p = sub.add_parser('create-investigation')
    common(p)
    p.add_argument('--title', required=True)
    p.add_argument('--description', required=True)
    p.add_argument('--priority', default='HIGH', choices=['HIGH', 'MEDIUM', 'LOW'])

    # get-investigation
    p = sub.add_parser('get-investigation')
    common(p)
    p.add_argument('--task-id', required=True)

    # list-investigations
    p = sub.add_parser('list-investigations')
    common(p)
    p.add_argument('--status', default=None,
                   choices=['PENDING_TRIAGE','LINKED','PENDING_START','IN_PROGRESS',
                            'PENDING_CUSTOMER_APPROVAL','COMPLETED','FAILED',
                            'TIMED_OUT','CANCELED','SKIPPED'])
    p.add_argument('--limit', type=int, default=None)

    # list-executions
    p = sub.add_parser('list-executions')
    common(p)
    p.add_argument('--task-id', required=True)
    p.add_argument('--limit', type=int, default=None)

    # list-recommendations
    p = sub.add_parser('list-recommendations')
    common(p)
    p.add_argument('--task-id', default=None)
    p.add_argument('--status', default=None,
                   choices=['PROPOSED','ACCEPTED','REJECTED','CLOSED','COMPLETED','UPDATE_IN_PROGRESS'])
    p.add_argument('--priority', default=None, choices=['HIGH', 'MEDIUM', 'LOW'])
    p.add_argument('--limit', type=int, default=None)

    args = parser.parse_args()

    dispatch = {
        'create-chat':          lambda: create_chat(args.role_arn, args.space_id, args.region, args.user_id),
        'send-message':         lambda: send_message(args.role_arn, args.space_id, args.execution_id, args.content, args.region, args.user_id),
        'list-chats':           lambda: list_chats(args.role_arn, args.space_id, args.max_results, args.region, args.user_id),
        'list-journal-records': lambda: list_journal_records(args.role_arn, args.space_id, args.execution_id, args.record_type, args.limit, args.order, args.region),
        'create-investigation': lambda: create_investigation(args.role_arn, args.space_id, args.title, args.description, args.priority, args.region),
        'get-investigation':    lambda: get_investigation(args.role_arn, args.space_id, args.task_id, args.region),
        'list-investigations':  lambda: list_investigations(args.role_arn, args.space_id, args.status, args.limit, args.region),
        'list-executions':      lambda: list_executions(args.role_arn, args.space_id, args.task_id, args.limit, args.region),
        'list-recommendations': lambda: list_recommendations(args.role_arn, args.space_id, args.task_id, args.status, args.priority, args.limit, args.region),
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)
    try:
        result = dispatch[args.command]()
    except AgentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if isinstance(result, str):
        print(result)
    else:
        jprint(result)


if __name__ == '__main__':
    main()
