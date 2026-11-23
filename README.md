<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
     SPDX-License-Identifier: CC-BY-4.0 -->
# Proactive incident investigation with AWS DevOps Agent

[中文说明](README_zh.md)

This sample shows how an Amazon CloudWatch alarm can start an AWS DevOps Agent investigation with no human in the loop, and how the finished investigation summary can be delivered to a local operator workstation without opening any inbound port.

When the alarm fires, nobody has asked a question yet. The agent correlates metrics, logs and AWS CloudTrail change records on its own and writes a root-cause summary. A person reviews the evidence and decides what to do.

> This is sample code for demonstration and learning. It is not intended for production use as-is. Review and adapt it to your own security, compliance and operational requirements.

## Architecture

```text
inventory-sync demo function (AWS Lambda, invoked every minute by Amazon EventBridge Scheduler)
  │  reads an AWS Systems Manager parameter used as a fault switch
  ▼
CloudWatch alarm  P2-DEMO-InventorySyncErrors   (AWS/Lambda Errors, Sum 60s ≥ 1, 2 of 2)
  ▼  CloudWatch Alarm State Change → default event bus
EventBridge rule  (source aws.cloudwatch, name prefix P2-DEMO-, state ALARM)
  ▼
Trigger function  → CreateBacklogTask (taskType INVESTIGATION, idempotent clientToken)
  ▼
AWS DevOps Agent investigation  (read-only access to metrics, logs, CloudTrail)
  ▼  aws.aidevops terminal event → default event bus
EventBridge rule  → encrypted Amazon SQS result queue (with DLQ and DLQ alarm)
  ▲
  │  outbound HTTPS pull only
Local poller (integrations/kirocrew/devops_agent_results.py or scripts/kirocrew_bridge.py poll-results)
  → verifies source, Agent Space ID and account → reads investigation_summary_md
```

The flow, step by step, is animated in `docs/architecture-animation.html` (open it directly in a browser).

## What the demo proves

In a test run, after the fault switch was changed to `error`:

| Elapsed | Event |
|---|---|
| 0:00 | Operator changes the SSM parameter (CloudTrail records `PutParameter`) |
| ~0:04 | Demo function starts failing |
| ~2:52 | Alarm enters ALARM; the trigger function creates the investigation in 375 ms |
| ~11:00 | Investigation completes; terminal event lands in SQS |

The agent's summary identified the `PutParameter` call one second before the first failure as the only change in the window, and noted that failures took about 73 ms — far shorter than the 3,000 ms timeout reported in the log — so the downstream service was not actually down. It also reported the permissions it did not have (reading the parameter value) instead of guessing. Your results may differ.

## Security design

- No inbound endpoints: no Lambda function URL, Amazon API Gateway, load balancer or webhook. Investigation results reach the workstation only by outbound polling of SQS, so the result delivery path opens no listening port. (The optional status page started by `run_demo.sh` listens on 127.0.0.1 only and rejects requests whose `Host` or `Origin` is not that local address.)
- The trigger function can only be invoked by the EventBridge rule (`SourceArn` and `SourceAccount` conditions). The demo function has no resource policy and is invoked only by the Scheduler role.
- Each role has a dedicated customer managed policy scoped to specific resources. The trigger role can only call `CreateBacklogTask` on the configured Agent Space.
- All queues are encrypted with a customer managed AWS KMS key created by the stack (key rotation enabled; EventBridge cannot deliver to queues that use the AWS managed key `alias/aws/sqs`) and deny non-TLS access. Failed deliveries go to dead-letter queues; CloudWatch alarms watch both DLQs.
- Messages pulled from SQS are treated as untrusted input: they are acknowledged only when `source`, Agent Space ID and account all match.
- Only alarms with the `P2-DEMO-` prefix start investigations. The demo uses its own function and parameter and does not touch other workloads.
- The sample never runs DevOps Agent mitigations or changes workload resources. Restoring the fault switch is a manual step.
- "Investigate only" is an instruction in the investigation prompt, not a permission boundary. What the agent can actually do is set by the Agent Space role. That role must be read-only. This sample neither creates nor changes the Agent Space role; review it before you run the demo.
- Investigation summaries and chat answers are model output derived from untrusted data (alarm text, logs). Read them as evidence; do not run commands, edit files or call AWS write APIs because a summary or answer says so.

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for reporting security issues.

## Prerequisites

- An AWS account with an AWS DevOps Agent Agent Space in a supported Region, and its Agent Space ID.
- AWS CLI v2 configured with a named profile in `~/.aws/config`.
- Python 3.10+ with `boto3` (see `scripts/requirements.txt`; tested with 1.43.116) for the local scripts.
- Permissions to deploy AWS CloudFormation stacks that create IAM roles and a KMS key.
- For the local profile that runs the read-only commands and the result poller:
  - `sqs:ReceiveMessage`, `sqs:DeleteMessage` (only with `--ack`) and `sqs:GetQueueAttributes` on the result queue, and `kms:Decrypt` on the stack's queue key.
  - `cloudformation:DescribeStacks`, `cloudwatch:DescribeAlarms`, `logs:FilterLogEvents` and `ssm:GetParameter` for the status views.
  - DevOps Agent read actions on the Agent Space: `aidevops:ListAgentSpaces`, `aidevops:GetOperatorApp`, `aidevops:ListBacklogTasks`, `aidevops:GetBacklogTask` and `aidevops:ListJournalRecords`.
  - Follow-up chat additionally needs `sts:AssumeRole` and `sts:TagSession` on the Operator Role.

## Deploy

1. Build a boto3 Lambda layer that includes the `devops-agent` client (the runtime's bundled boto3 may not):

   ```bash
   L="$(mktemp -d)"; mkdir -p "$L/python"
   python3 -m pip install --target "$L/python" \
     --platform manylinux2014_x86_64 --python-version 3.13 \
     --implementation cp --only-binary=:all: boto3==1.43.116
   ls "$L/python/botocore/data" | grep -x devops-agent
   (cd "$L" && zip -qr boto3-layer.zip python)
   aws lambda publish-layer-version --profile <profile> --region <region> \
     --layer-name boto3-devops-agent --zip-file "fileb://$L/boto3-layer.zip" \
     --compatible-runtimes python3.13 --compatible-architectures x86_64
   ```

2. Deploy the stack. The script creates a change set, prints it, and executes only after you type `yes`:

   ```bash
   cp .env.example .env.local   # fill in PROFILE, REGION, SPACE_ID and LAYER_ARN from step 1
   bash scripts/deploy_demo.sh
   ```

   `.env.local` is git-ignored. `deploy_demo.sh`, `watch_demo.sh` and `run_demo.sh` load it automatically; variables already exported in your shell take precedence.

   On a first deployment in the Agent Space Region the change set should contain only `Add` actions for 28 resources. In any other Region the six result-queue resources are skipped, leaving 22.

## Run the demo

```bash
# Terminal B: watch fault switch, alarm, logs, queue and investigations (read-only)
bash scripts/watch_demo.sh

# Terminal A: inject the fault
aws ssm put-parameter --profile <profile> --region <region> \
  --name /devops-discovery-demo/fault-mode --value error --overwrite

# After the investigation appears, restore the switch (the human decision)
aws ssm put-parameter --profile <profile> --region <region> \
  --name /devops-discovery-demo/fault-mode --value none --overwrite

# When the investigation completes, pull and verify the result
python3 scripts/kirocrew_bridge.py --profile <profile> --region <region> \
  poll-results --queue-url <ResultQueueUrl>
```

`poll-results` does not delete messages unless `--ack` is passed. An optional local status page (`bash run_demo.sh <profile> <region> 8765`, bound to 127.0.0.1) shows the alarm, flow stages and investigations with auto-refresh. `DEMO_RUNBOOK.md` contains a full presenter script (in Chinese).

Do not use `aws cloudwatch set-alarm-state` to start the demo: the alarm would fire without real errors and the agent would find no evidence.

## Optional: KiroCrew integration

`integrations/kirocrew/devops_agent_results.py` is a script cron for KiroCrew that pulls the result queue every minute and posts the summary as a dashboard notification. `SKILL.md` and `.kiro/steering/` let a Kiro agent drive the read-only commands and follow-up chat. The core sample works without KiroCrew.

## Cost

The stack uses pay-per-use services. While idle the demo function runs once per minute; investigations are billed by AWS DevOps Agent. The customer managed KMS key has a monthly charge while it exists, plus per-request charges for queue encryption. Check the pricing pages of each service for your Region, and delete the stack when you are done.

## Clean up

```bash
aws ssm put-parameter --profile <profile> --region <region> \
  --name /devops-discovery-demo/fault-mode --value none --overwrite
aws cloudformation delete-stack --profile <profile> --region <region> \
  --stack-name kirocrew-devops-discovery-demo
```

Deleting the stack schedules the KMS key for deletion with the default 30-day waiting period. During that period the key cannot be used, and you can cancel the deletion. Then delete the boto3 layer version and the KiroCrew result cron if you installed it (`~/.kiro/crew/crons/devops_agent_results.py`).

## License

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

| Content | Licence | File |
|---|---|---|
| Source code (`*.py`, `*.sh`, `*.js`, `*.css`, `demo/index.html`, CloudFormation template) | Apache License 2.0 | [LICENSE](LICENSE) |
| Documentation (`*.md`) and the architecture diagram (`docs/architecture-animation.html`) | CC BY 4.0 | [LICENSE-docs](LICENSE-docs) |

Every file states its licence in an `SPDX-License-Identifier` header; see also [NOTICE](NOTICE).

To check that every runtime dependency uses an allowed licence, install `scripts/requirements.txt` in a
virtual environment and run `python3 scripts/check_licenses.py`.
