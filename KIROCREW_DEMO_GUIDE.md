<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
     SPDX-License-Identifier: CC-BY-4.0 -->
# KiroCrew × 亚马逊云科技 DevOps Agent 主动发现 Demo

## 演示要表达什么

故障信号出现后，没有人提问，DevOps Agent 自动开始调查，并关联指标、日志和配置变更给出根因；KiroCrew 把正式结论送到 Dashboard，再支持后续追问。

```text
inventory-sync 演示函数（每分钟运行）
  → 切换 SSM 故障开关，函数真实报错
  → P2-DEMO-InventorySyncErrors 告警
  → EventBridge → Trigger Lambda → DevOps Agent Investigation
  → 调查终态事件 → EventBridge → 加密 SQS
  → KiroCrew 定时脚本出站拉取 → investigation_summary_md → Dashboard
```

Agent 能找到的证据都是真实的：Lambda `Errors` 指标、函数日志里的 `inventory-service timeout`，以及 CloudTrail 中 SSM 参数被修改的记录。

## 安全设计

- 没有 Function URL、API Gateway、ALB、Hook 或任何入站端点；结果投递链路只靠本机出站拉取 SQS，不在本机开放端口。可选状态页只监听 127.0.0.1，并拒绝 `Host` 或 `Origin` 不是本机地址的请求。
- 两个 Lambda 都由 AWS 服务调用：Trigger 由 EventBridge 调用，权限限定 `SourceArn` 和 `SourceAccount`；演示函数由 EventBridge Scheduler 通过专用角色调用。
- 调查结果写入加密 SQS，由本机通过出站 HTTPS 拉取。
- 队列使用 Stack 创建的客户自管 KMS 密钥加密，并拒绝非 TLS 访问；结果队列配置 DLQ，失败 5 次进入 DLQ 并触发告警；触发 DLQ 也配置了告警。本机 Profile 拉取结果需要该密钥的 `kms:Decrypt`。
- “只调查”只写在调查提示里，不是权限边界。Agent Space 的角色必须是只读的；本示例不创建也不修改该角色。
- 调查摘要和 Chat 回答按不可信数据处理，不据此执行命令、修改文件或调用 AWS 写操作。
- 每个角色使用独立最小权限托管策略，没有通配资源。
- 演示函数只读取自己的故障开关、只写自己的日志组，保留并发为 2。
- 只匹配 `P2-DEMO-` 告警，不影响业务告警。
- 不执行 mitigation，不修改业务资源。
- 只读查询直接使用本机 Profile，不需要 `sts:TagSession`。

## 一、核对本机配置

```bash
cd <repo-root>
python3 scripts/kirocrew_bridge.py --profile default --region us-east-1 discover
```

输出示例：`<profile>` → 账号 `<account-id>`，Agent Space `<agent-space-name>`（`<agent-space-id>`），区域 `<region>`。

## 二、部署（由人在终端执行）

KiroCrew 安全策略禁止智能体执行 CloudFormation 部署，这一步由人完成。

### 1. 准备 boto3 Layer

Lambda 运行时自带的 boto3 不一定包含 `devops-agent` 客户端，需要打包新版本。必须按 Lambda 的平台下载依赖：

```bash
L="$(mktemp -d)"; mkdir -p "$L/python"
python3 -m pip install --target "$L/python" \
  --platform manylinux2014_x86_64 --python-version 3.13 \
  --implementation cp --only-binary=:all: boto3==1.43.116
ls "$L/python/botocore/data" | grep -x devops-agent   # 必须输出 devops-agent
(cd "$L" && zip -qr boto3-layer.zip python)
aws lambda publish-layer-version --profile default --region us-east-1 \
  --layer-name boto3-devops-agent \
  --zip-file "fileb://$L/boto3-layer.zip" \
  --compatible-runtimes python3.13 --compatible-architectures x86_64
```

发布后得到的 ARN 形如 `arn:aws:lambda:<region>:<account-id>:layer:boto3-devops-agent:<version>`（验证使用的是 boto3 1.43.116；Layer 不要跨账号共享）。

### 2. 运行部署脚本

```bash
cd <repo-root>
cp .env.example .env.local     # 首次：填入 PROFILE、REGION、SPACE_ID、LAYER_ARN
bash scripts/deploy_demo.sh
```

账号相关的值只放在 `.env.local`，该文件已被 `.gitignore` 排除，不会进入仓库；shell 里已导出的同名变量优先。`run_demo.sh`、`scripts/watch_demo.sh` 也读取同一个文件。

脚本先创建 Change Set 并列出变更，输入 `yes` 才执行。在 Agent Space 所在区域首次部署时，变更应全部为 `Add`，共 28 个资源（在其他区域部署会跳过 6 个结果队列相关资源，共 22 个）：

| 资源类型 | 数量 |
|---|---|
| `AWS::Lambda::Function` | 2 |
| `AWS::Lambda::Permission` | 1 |
| `AWS::Scheduler::Schedule` | 1 |
| `AWS::SSM::Parameter` | 1 |
| `AWS::SQS::Queue` | 3 |
| `AWS::SQS::QueuePolicy` | 3 |
| `AWS::Events::Rule` | 2 |
| `AWS::IAM::Role` | 3 |
| `AWS::IAM::ManagedPolicy` | 3 |
| `AWS::CloudWatch::Alarm` | 4 |
| `AWS::Logs::LogGroup` | 2 |
| `AWS::Lambda::EventInvokeConfig` | 1 |
| `AWS::KMS::Key` | 1 |
| `AWS::KMS::Alias` | 1 |

出现 `Modify`、`Remove` 或其他类型时不要输入 `yes`。脚本会删除 Change Set 并退出。

### 3. 记录输出

脚本结束时会打印 Outputs。需要用到的是 `ResultQueueUrl`，`DemoAlarmName` 应为 `P2-DEMO-InventorySyncErrors`。

## 三、注册结果拉取定时任务

先把仓库里的拉取脚本复制到 KiroCrew 的 cron 目录：

```bash
mkdir -p ~/.kiro/crew/crons
cp integrations/kirocrew/devops_agent_results.py ~/.kiro/crew/crons/
```

然后在 KiroCrew 中添加脚本型 cron（零模型调用，只在有结果时通知）：

- 脚本：`~/.kiro/crew/crons/devops_agent_results.py:run`
- 间隔：60 秒
- message：

```json
{"profile":"<profile>","region":"<region>","queueUrl":"<ResultQueueUrl>","project":"<repo-root>"}
```

可先试跑（`kirocrew cron preview` 在 kirocrew 0.12.2 中验证可用）：

```bash
kirocrew cron preview ~/.kiro/crew/crons/devops_agent_results.py:run -m '<上面的 JSON>'
```

试跑通过后注册：

```bash
kirocrew cron add devops-agent-results '<上面的 JSON>' --every 60 \
  --script ~/.kiro/crew/crons/devops_agent_results.py:run
```

## 四、演示前预跑一次真实调查（至少提前 30 分钟）

调查需要几分钟，现场不要空等。先完整跑一遍：

```bash
aws ssm put-parameter --profile default --region us-east-1 \
  --name /devops-discovery-demo/fault-mode --value error --overwrite
```

约 2–3 分钟后告警进入 ALARM，Investigation 自动创建。确认任务出现后恢复：

```bash
aws ssm put-parameter --profile default --region us-east-1 \
  --name /devops-discovery-demo/fault-mode --value none --overwrite
```

等待调查完成，记下 Task ID，确认正式摘要可读：

```bash
python3 scripts/kirocrew_bridge.py --profile default --region us-east-1 latest-completed
python3 scripts/kirocrew_bridge.py --profile default --region us-east-1 summary --task-id <TaskId>
```

## 五、现场流程（10–12 分钟）

启动辅助状态页（只绑定 127.0.0.1，与云端无关）：

```bash
bash run_demo.sh default us-east-1 8765
```

| 时间 | 动作 | 讲解要点 |
|---|---|---|
| 0:00–1:30 | 展示架构与状态页 | 没有入站端口；调查只读；变更需人工批准 |
| 1:30–2:30 | 执行 `put-parameter ... --value error` | 模拟一次“配置变更导致下游超时”，没有人去提问 |
| 2:30–5:00 | 页面每 15 秒自动刷新：告警行变红（ALARM），新调查带 🆕 NEW 标记出现 | 告警自动触发了 Investigation，全程没有人操作 |
| 5:00–8:00 | 打开预跑任务的正式摘要 | 根因：12:36:59 有人 `PutParameter` 把开关改为 `error`，1 秒后故障开始；Agent 还根据约 73 ms 的快速失败判断这是注入故障，而不是下游真的宕机 |
| 8:00–9:30 | 展示 Dashboard 收到的 cron 通知 | 结果是本机出站拉取的，不需要暴露 Hook |
| 9:30–11:00 | Chat 追问证据 | 例如“为什么判断是配置变更导致，而不是容量不足？” |
| 11:00–12:00 | 恢复 `--value none` 并收尾 | 处置动作由人决定 |

## 六、兜底

- 告警未及时触发：展示预跑调查和对应的 cron 通知。
- 调查未完成：展示 Task ID 和当前状态，不提前下结论。
- Chat 不可用（缺 `sts:TagSession`）：跳过追问环节，主动发现主线不受影响。
- 收到无法核验的事件：cron 会通知并保留消息，最终进入 DLQ。

## 七、清理

演示结束后先确认故障开关是 `none`。删除 Stack 由人执行：

```bash
aws cloudformation delete-stack --profile default --region us-east-1 \
  --stack-name kirocrew-devops-discovery-demo
```

删除 Stack 后，KMS 密钥会进入默认 30 天的删除等待期，期间不可用，也可以取消删除；密钥存在期间按月计费。Layer `boto3-devops-agent`、KiroCrew 结果拉取 cron（`kirocrew cron remove <job-id>`）和 `~/.kiro/crew/crons/devops_agent_results.py` 需要单独删除。

## 端到端实测（2026-10-21，演示账号，us-east-1）

| 时间 | 事件 |
|---|---|
| 20:36:56 | `fault-mode` 改为 `error` |
| 20:39:48 | 告警 `P2-DEMO-InventorySyncErrors` 进入 ALARM |
| 20:39:5x | Trigger Lambda 自动创建 Investigation（耗时 375 ms） |
| 20:45:57 | 人工把 `fault-mode` 恢复为 `none` |
| 20:47:48 | 调查 COMPLETED；告警同时恢复 OK |
| 20:51 | 结果队列收到 1 条终态事件；`kirocrew cron preview` 核验通过，读取正式摘要并生成通知，消息已确认删除 |

DevOps Agent 的正式结论：

- **根因**：演示操作者的 IAM 用户在 12:36:59Z 手工执行 `PutParameter`，把 `/devops-discovery-demo/fault-mode` 从 `none` 改为 `error`。这是窗口内唯一的变更，比故障开始早 1 秒。
- **机制**：函数每次运行都读取这个参数，值为 `error` 时直接抛出 `TimeoutError`。约 73 ms 就失败，远低于真实超时所需的 3000 ms，所以判定为注入故障，不是下游真实宕机。
- **处置参考**：把参数恢复为 `none` 即可停止复发。
- **权限边界**：Agent 角色读取 SSM 参数和 Lambda 配置被拒绝（AccessDenied），它仅凭 CloudTrail 和日志就得出了确定的根因，并说明修复需要它不具备的权限。这一点可以在现场用来说明“Agent 只读，处置由人执行”。

安全核对（当时的模板版本）：全部资源 CREATE_COMPLETE；两个 Lambda 均无 Function URL；Trigger 只允许 EventBridge 在本账号、本规则下调用；演示函数没有资源策略，只能由 Scheduler 角色调用；所有队列加密并拒绝非 TLS；触发 DLQ 和结果 DLQ 均为空。
