<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
     SPDX-License-Identifier: CC-BY-4.0 -->
# Chat 在“所有区域”查询时连接中断（观察记录）

这是一次测试中观察到的现象，不是服务文档中的限制说明。服务行为可能随版本变化。

## 现象

调用 `send-message` 要求查询“所有区域”时，输出停在：

```
Now let me query all regions for EC2 instances in parallel:ERROR: A connection error occurred. Please try again.
```

- 把 boto3 的 `read_timeout` 调到 600 秒仍然复现。
- 脚本正常退出（exit code 0），错误来自响应流中的 `responseFailed` 事件。

## 测试记录（旧环境，账号标识已移除）

| 查询 | 结果 |
|------|------|
| 「列出 us-east-1 和 us-west-2 中 CPU < 30% 的机器」 | 成功，约 30 秒返回 |
| 「列出所有区域 CPU < 30% 的机器」（3 次重试） | 每次都在并行查询那一步中断 |

## 观察到的情况

- 中断发生在 Agent 开始并行查询多个区域时，增大客户端读取超时没有改善。
- 在同一个 `executionId` 上重试，或换一个新的 `executionId` 重试，结果相同。
- 把范围缩小到少数几个区域后，同样的问题可以正常返回。

## 可行的做法

- 在 Chat 中把问题限定在少数几个区域。
- 需要覆盖整个账号或所有区域时，改为创建 Investigation（`create-backlog-task`，`taskType=INVESTIGATION`）。它异步执行，结果通过任务状态和正式摘要读取，不依赖 Chat 的流式连接。
