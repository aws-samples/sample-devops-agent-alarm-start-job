<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
     SPDX-License-Identifier: CC-BY-4.0 -->
# 使用 AWS DevOps Agent 主动调查故障

[English](README.md)

本示例展示 Amazon CloudWatch 告警如何在无人参与的情况下自动启动 AWS DevOps Agent 调查，以及调查完成后如何在不开放任何入站端口的前提下，把正式摘要送到本地值班人员手中。

告警触发时，还没有人提问。Agent 自行关联指标、日志和 AWS CloudTrail 变更记录，写出根因摘要；由人审阅证据并决定如何处置。

> 本示例代码用于演示和学习，不建议直接用于生产环境。请结合自身的安全、合规和运维要求审阅并调整。

## 架构

```text
inventory-sync 演示函数（AWS Lambda，由 Amazon EventBridge Scheduler 每分钟调用）
  │  读取一个用作故障开关的 AWS Systems Manager 参数
  ▼
CloudWatch 告警 P2-DEMO-InventorySyncErrors（AWS/Lambda Errors，60 秒求和 ≥ 1，连续 2 个周期）
  ▼  CloudWatch Alarm State Change → 默认事件总线
EventBridge 规则（source=aws.cloudwatch，名称前缀 P2-DEMO-，新状态 ALARM）
  ▼
触发函数 → CreateBacklogTask（taskType=INVESTIGATION，幂等 clientToken）
  ▼
AWS DevOps Agent 调查（只读访问指标、日志和 CloudTrail）
  ▼  aws.aidevops 终态事件 → 默认事件总线
EventBridge 规则 → 加密的 Amazon SQS 结果队列（配置 DLQ 和 DLQ 告警）
  ▲
  │  仅出站 HTTPS 拉取
本机拉取脚本（integrations/kirocrew/devops_agent_results.py 或 scripts/kirocrew_bridge.py poll-results）
  → 核验来源、Agent Space ID 和账号 → 读取 investigation_summary_md
```

逐步流转动画见 `docs/architecture-animation.html`，直接用浏览器打开即可。

## 演示能说明什么

一次测试中，把故障开关改为 `error` 之后：

| 经过时间 | 事件 |
|---|---|
| 0:00 | 人修改 SSM 参数（CloudTrail 记录 `PutParameter`） |
| 约 0:04 | 演示函数开始报错 |
| 约 2:52 | 告警进入 ALARM；触发函数在 375 ms 内创建调查 |
| 约 11:00 | 调查完成，终态事件进入 SQS |

Agent 的摘要把首次失败前 1 秒的 `PutParameter` 识别为时间窗口内唯一的变更；并指出每次失败只用了约 73 ms，远短于日志中声称的 3000 ms 超时，因此下游服务并没有真的宕机。对于自身缺少的权限（读取参数值），它在报告中如实说明，没有猜测。实际结果可能不同。

## 安全设计

- 没有入站端点：不使用 Lambda 函数 URL、Amazon API Gateway、负载均衡器或 Webhook。调查结果只通过本机出站拉取 SQS 获得，结果投递链路不在本机监听任何端口。（`run_demo.sh` 启动的可选状态页只监听 127.0.0.1，并拒绝 `Host` 或 `Origin` 不是该本机地址的请求。）
- 触发函数只能由这条 EventBridge 规则调用（限定 `SourceArn` 和 `SourceAccount`）；演示函数没有资源策略，只能由 Scheduler 角色调用。
- 每个角色使用独立的客户托管策略，资源范围具体到单个资源；触发角色只能在指定 Agent Space 上调用 `CreateBacklogTask`。
- 所有队列使用 Stack 创建的客户自管 AWS KMS 密钥加密（已启用密钥轮换；EventBridge 无法投递到使用 AWS 托管密钥 `alias/aws/sqs` 的队列），并拒绝非 TLS 访问；投递失败进入死信队列，两个 DLQ 都配置了 CloudWatch 告警。
- 从 SQS 拉取的消息按不可信输入处理：`source`、Agent Space ID 和账号全部匹配才确认删除。
- 只有前缀为 `P2-DEMO-` 的告警会启动调查；演示使用独立的函数和参数，不影响其他工作负载。
- 示例不会执行 DevOps Agent 的缓解操作，也不修改工作负载资源；恢复故障开关由人手动完成。
- “只调查”是写在调查提示里的要求，不是权限边界。Agent 实际能做什么由 Agent Space 的角色决定，该角色必须是只读的。本示例不会创建或修改 Agent Space 角色，运行演示前请自行核查。
- 调查摘要和 Chat 回答是模型基于不可信数据（告警文本、日志）生成的输出，只能作为证据阅读；不要因为摘要或回答的内容去执行命令、修改文件或调用 AWS 写操作。

安全问题的报告方式见 [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications)。

## 前提条件

- 一个亚马逊云科技账号，在受支持的区域中已创建 AWS DevOps Agent 的 Agent Space，并知道其 Agent Space ID。
- 已安装 AWS CLI v2，并在 `~/.aws/config` 中配置命名 Profile。
- 本机脚本需要 Python 3.10 及以上版本和 `boto3`（见 `scripts/requirements.txt`，已测试版本 1.43.116）。
- 具有部署 AWS CloudFormation Stack（会创建 IAM 角色和 KMS 密钥）的权限。
- 运行只读命令和结果拉取的本机 Profile 需要：
  - 结果队列上的 `sqs:ReceiveMessage`、`sqs:DeleteMessage`（仅 `--ack` 时）、`sqs:GetQueueAttributes`，以及 Stack 队列密钥上的 `kms:Decrypt`。
  - 状态查看用的 `cloudformation:DescribeStacks`、`cloudwatch:DescribeAlarms`、`logs:FilterLogEvents`、`ssm:GetParameter`。
  - Agent Space 上的 DevOps Agent 读取操作：`aidevops:ListAgentSpaces`、`aidevops:GetOperatorApp`、`aidevops:ListBacklogTasks`、`aidevops:GetBacklogTask`、`aidevops:ListJournalRecords`。
  - 后续 Chat 追问另外需要 Operator Role 上的 `sts:AssumeRole` 和 `sts:TagSession`。

## 部署

1. 构建包含 `devops-agent` 客户端的 boto3 Lambda Layer（运行时自带的 boto3 不一定包含）：

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

2. 部署 Stack。脚本会先创建并打印 Change Set，输入 `yes` 后才执行：

   ```bash
   cp .env.example .env.local   # 填入 PROFILE、REGION、SPACE_ID，以及第 1 步得到的 LAYER_ARN
   bash scripts/deploy_demo.sh
   ```

   `.env.local` 已被 git 忽略。`deploy_demo.sh`、`watch_demo.sh`、`run_demo.sh` 都会自动读取它；shell 中已导出的同名变量优先。

   在 Agent Space 所在区域首次部署时，Change Set 中应只有 28 个 `Add` 操作；在其他区域部署时会跳过 6 个结果队列相关资源，共 22 个。

## 运行演示

```bash
# 终端 B：只读观察故障开关、告警、日志、队列和调查
bash scripts/watch_demo.sh

# 终端 A：注入故障
aws ssm put-parameter --profile <profile> --region <region> \
  --name /devops-discovery-demo/fault-mode --value error --overwrite

# 出现新调查后，恢复开关（由人做出的决定）
aws ssm put-parameter --profile <profile> --region <region> \
  --name /devops-discovery-demo/fault-mode --value none --overwrite

# 调查完成后，拉取并核验结果
python3 scripts/kirocrew_bridge.py --profile <profile> --region <region> \
  poll-results --queue-url <ResultQueueUrl>
```

不加 `--ack` 时，`poll-results` 不会删除消息。可选的本地状态页（`bash run_demo.sh <profile> <region> 8765`，只绑定 127.0.0.1）会自动刷新显示告警、流程状态和调查任务。完整的现场操作手册见 `DEMO_RUNBOOK.md`。

不要用 `aws cloudwatch set-alarm-state` 启动演示：告警虽然会触发，但没有真实报错，Agent 找不到任何证据。

## 可选：KiroCrew 集成

`integrations/kirocrew/devops_agent_results.py` 是 KiroCrew 的脚本型定时任务，每分钟拉取一次结果队列，并把摘要发成 Dashboard 通知。`SKILL.md` 和 `.kiro/steering/` 让 Kiro 智能体可以执行只读命令和后续追问。不使用 KiroCrew 时，核心示例同样可以运行。

## 费用

Stack 使用按量计费的服务。空闲时演示函数每分钟运行一次；调查按 AWS DevOps Agent 的计费方式收费。客户自管 KMS 密钥存在期间按月收费，队列加解密另按请求次数收费。请查阅各服务在所用区域的价格页面，演示结束后删除 Stack。

## 清理

```bash
aws ssm put-parameter --profile <profile> --region <region> \
  --name /devops-discovery-demo/fault-mode --value none --overwrite
aws cloudformation delete-stack --profile <profile> --region <region> \
  --stack-name kirocrew-devops-discovery-demo
```

删除 Stack 后，KMS 密钥会进入默认 30 天的删除等待期；等待期内密钥不可用，也可以取消删除。然后删除 boto3 Layer 版本；如果安装过 KiroCrew 结果拉取 cron，也一并删除（`~/.kiro/crew/crons/devops_agent_results.py`）。

## 许可证

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

| 内容 | 许可 | 文件 |
|---|---|---|
| 源代码（`*.py`、`*.sh`、`*.js`、`*.css`、`demo/index.html`、CloudFormation 模板） | Apache License 2.0 | [LICENSE](LICENSE) |
| 文档（`*.md`）和架构图（`docs/architecture-animation.html`） | CC BY 4.0 | [LICENSE-docs](LICENSE-docs) |

每个文件都用 `SPDX-License-Identifier` 许可头标明许可，另见 [NOTICE](NOTICE)。

检查运行时依赖的许可：在虚拟环境中安装 `scripts/requirements.txt` 后运行 `python3 scripts/check_licenses.py`。
