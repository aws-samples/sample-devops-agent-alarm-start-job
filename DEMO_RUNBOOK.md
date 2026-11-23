<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
     SPDX-License-Identifier: CC-BY-4.0 -->
# 现场操作手册：命令行 × 网页 × 控制台

每一步都标明三处应该看到的变化：**终端**（执行和观察）、**网页**（状态页与架构动画）、**控制台**（亚马逊云科技控制台中的证据）。

所有命令在仓库根目录下执行，Profile 为 `<profile>`，区域为 `<region>`（演示环境使用 `us-east-1`）。

## 一、屏幕布局（开场前 15 分钟摆好）

| 位置 | 内容 | 打开方式 |
|---|---|---|
| 左上 | 终端 A：执行命令 | 新开终端窗口 |
| 左下 | 终端 B：实时观察 | `bash scripts/watch_demo.sh` |
| 右侧浏览器标签 1 | 架构动画 | 用浏览器打开 `docs/architecture-animation.html` |
| 标签 2 | 状态页 | `bash run_demo.sh default us-east-1 8765` 后打开 http://127.0.0.1:8765 |
| 标签 3–7 | 控制台 | 见下方链接，提前登录部署演示环境的同一账号 |

控制台标签（全部为 us-east-1）：

- 告警：[P2-DEMO-InventorySyncErrors](https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#alarmsV2:alarm/P2-DEMO-InventorySyncErrors)
- 演示函数日志：[inventory-sync 日志组](https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/$252Faws$252Flambda$252Fdevops-discovery-demo-inventory-sync)
- Trigger 监控：[devops-discovery-demo-trigger](https://us-east-1.console.aws.amazon.com/lambda/home?region=us-east-1#/functions/devops-discovery-demo-trigger?tab=monitoring)
- 变更记录：[CloudTrail PutParameter 事件](https://us-east-1.console.aws.amazon.com/cloudtrailv2/home?region=us-east-1#/events?EventName=PutParameter)
- DevOps Agent：控制台搜索“DevOps Agent”→ Agent Space `<agent-space-name>` → 打开 Web App 的 Investigations 列表

## 二、开场前检查（终端 A）

```bash
aws ssm get-parameter --profile default --region us-east-1 \
  --name /devops-discovery-demo/fault-mode --query Parameter.Value --output text
```

必须是 `none`。同时确认：

- 终端 B：告警状态为 `OK`，函数日志里是 `"faultMode": "none"`。
- 状态页：告警行为绿色“正常（OK）”，右上角“自动刷新 · 上次 hh:mm:ss”在走。
- 已有一条预跑完成的调查（演练前记下它的 TaskId），用于第五幕。

## 三、现场步骤

### 第 1 步：讲架构（0:00–1:30）

- **网页**：架构动画点“全景架构”，讲 4 张优势卡片；再点“播放”让动画自动走一遍。
- **讲解**：本机不开放任何入站端口；所有 Lambda 只由亚马逊云科技服务调用；调查只读。

### 第 2 步：注入故障（1:30–2:00）

- **终端 A**：

```bash
aws ssm put-parameter --profile default --region us-east-1 \
  --name /devops-discovery-demo/fault-mode --value error --overwrite
```

- **终端 B**：故障开关变为 `error`。
- **网页**：切到状态页，告警行仍是绿色。
- **讲解**：模拟一次有风险的配置变更。接下来没有人会去提问。

### 第 3 步：故障显现（2:00–3:00）

- **终端 B**：1 分钟内，函数最新日志变成 `"level": "ERROR" … inventory-service timeout`。
- **控制台**：演示函数日志组中出现 ERROR 记录。
- **讲解**：这是真实的 Lambda 报错，不是模拟数据。

### 第 4 步：主动发现（3:00–5:00）

- **终端 B**：约 3 分钟后告警状态变为 `ALARM`，“最近的调查”出现一条新的 `PENDING_START` 或 `IN_PROGRESS`。
- **网页**：状态页告警行自动变红，几秒内新调查带 🆕 NEW 标记出现，任务框高亮。全程不点任何按钮。
- **控制台**：
  - 告警页显示 In alarm。
  - Trigger 监控页的 Invocations 出现 1 次调用。
  - DevOps Agent Web App 中出现标题为 `CloudWatch Alarm: P2-DEMO-InventorySyncErrors` 的调查。
- **讲解**：告警自己触发了调查，从 ALARM 到创建调查只用了几百毫秒。

### 第 5 步：讲证据和结论（5:00–8:00）

新调查需要 5–8 分钟，不要等，直接展示预跑结果。

- **终端 A**：

```bash
python3 scripts/kirocrew_bridge.py --profile default --region us-east-1 \
  summary --task-id <TaskId>
```

- **控制台**：在 CloudTrail 中打开同一时间的 `PutParameter` 事件，展示操作者、时间和参数名，与摘要中的根因对照。
- **网页**：架构动画切到第 5 步“证据关联”。
- **讲解**：
  - 根因：12:36:59 的 `PutParameter`，比故障早 1 秒，是窗口内唯一的变更。
  - 机制：约 73 ms 就失败，远低于真实超时的 3000 ms，所以判定为注入故障，而不是下游真的宕机。
  - 权限边界：Agent 读取 SSM 参数被拒绝，它只靠 CloudTrail 和日志就得出了结论。

### 第 6 步：结果回推（8:00–9:30）

- **终端 A**：

```bash
kirocrew cron preview ~/.kiro/crew/crons/devops_agent_results.py:run -m \
'{"profile":"<profile>","region":"<region>","queueUrl":"<ResultQueueUrl>","project":"<repo-root>"}'
```

  队列里有终态事件时，输出完整通知（根因、机制、处置参考）；没有时输出为空，说明新调查还没完成。

- **终端 B**：结果队列消息数先为 1，处理后回到 0。
- **网页**：架构动画切到第 6 步“结果回推”。
- **讲解**：结果是本机定时出站拉取的，本机不需要暴露任何回调地址。

### 第 7 步：Chat 追问（9:30–11:00，可选）

需要 Operator Role 补齐 `sts:TagSession` 后才能使用；未补齐时跳过，不影响主线。

```bash
python3 scripts/kirocrew_bridge.py --profile default --region us-east-1 \
  chat --question '为什么判断是配置变更导致的，而不是下游服务容量不足？'
```

### 第 8 步：人工处置（11:00–12:00）

- **终端 A**：

```bash
aws ssm put-parameter --profile default --region us-east-1 \
  --name /devops-discovery-demo/fault-mode --value none --overwrite
```

  也可以在 Systems Manager → Parameter Store 控制台手工修改，让观众看到“人在操作”。

- **终端 B**：开关变回 `none`，1 分钟内日志恢复 INFO，约 2–3 分钟后告警回到 `OK`。
- **网页**：状态页告警行自动变回绿色；架构动画切到第 8 步“人工处置”。
- **讲解**：调查是 Agent 自动完成的，处置决定是人做的，恢复结果又由系统自动确认。

## 四、结束后

1. 终端 A 再次确认开关为 `none`。
2. 终端 B 按 `Ctrl+C` 停止观察。
3. 状态页终端按 `Ctrl+C` 停止服务。

## 五、现场兜底

| 情况 | 处理 |
|---|---|
| 告警 5 分钟仍未进入 ALARM | 用控制台告警页确认指标在上升；直接展示预跑调查 |
| 新调查未完成 | 展示 Task ID 和 IN_PROGRESS 状态，讲解用预跑结果 |
| `cron preview` 无输出 | 说明新调查尚未完成，展示预跑摘要 |
| 网络或凭证失效 | 用架构动画逐步讲解全流程 |
