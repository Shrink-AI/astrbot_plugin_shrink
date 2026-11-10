# 插件通信协议 v1

插件自己拥有业务、存储、凭据和生命周期。核心只保留通用工具发现、任务编排、人格与记忆，不能为新插件加入专用业务适配器。

## 发现和调用

AstrBot 激活插件实例可实现两个方法：同步 `plugin_capabilities()` 返回描述，异步 `invoke_capability(request)` 返回结果。协议客户端扫描宿主注册表，不导入其他插件，不比较具体提供者名；每次调用重新解析实例。客户端源文件随各插件分发，契约测试校验副本一致，无额外基础插件依赖。

```json
{"protocol":1,"capabilities":[{"name":"example.read","version":1,"description":"读取示例","actions":{"read":{"readonly":true,"description":"读取内容","parameters":{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]}}}}]}
```

```json
{"protocol":1,"capability":"example.read","version":1,"action":"read","arguments":{"id":"item"},"scope":{"admin":true,"session":"原始会话标识","sender":"创建者标识","message_id":"原始消息标识"}}
```

```json
{"protocol":1,"ok":true,"data":{"value":"实际结果"}}
```

只传 JSON 数据，不传宿主对象、模型对象、数据库或回调。参数定义用于发现和展示，提供者仍必须校验参数及权限，不能把描述当授权。异常、超时或 `ok:false` 明确返回失败证据；客户端不自动重试写操作。未知版本、未启用提供者、多个同版本提供者均明确报错；多个提供者必须指定发现结果中的 `provider`。

此协议用于同一 AstrBot 进程内的可信插件，不是安全沙箱。`scope` 由工具入口或已鉴权后台生成，不从模型 arguments 中获取。每个提供者保留管理员、创建者和会话校验。后台当前只接受已配置管理员私聊范围。电脑任务提交使用原始消息 ID 去重，后台提交需给稳定 request_id。

## 当前能力

| 能力 | 所有者 | 内容 |
| --- | --- | --- |
| message.compose | 石英核心 | 按当前人格表达工具事实，不发送、不写记忆 |
| projects.crafttable | 项目工具插件 | 发现和调用 CLI 工具，按实际成员权限选择空间 |
| projects.gitea | 项目工具插件 | 按账号权限及现有项目约束调用 CLI |
| computer.control | 桌面接入插件 | 配对设备、Agent 会话控制、确认与监工 |
| computer.tasks | 桌面接入插件 | 提交与查询电脑任务，原会话回传 |
| computer.project | 桌面接入插件 | 在授权设备上执行受限制的项目 CLI |
| reminders.manage | 提醒插件 | 创建、查看、取消提醒及回执 |

旧原生工具名继续可用；`shrink_deliver_reminder` 为兼容已保存 Cron 任务而保留，但注册方是提醒插件。石英以 `shrink_plugin_capability` 发现和调用协议能力，后台“插件公开能力”使用同一目录和参数。正常工具结果继续经过石英筛选整理后才可入记忆。

## 提醒迁移

部署先停用提醒插件、关闭旧核心的发送恢复定时器，再把 reminder_deliveries、reminder_series 连同原始 ID、状态、重试时间、消息 ID 逐行复制到提醒插件的 reminders.db，比较完整行及数量后切换。旧核心表只作备份，不再写入。自定义提醒提示词迁移到提醒插件数据目录的 prompts.json。新插件启动后重建恢复定时器；不重建用户任务、不改到期时间、不补发历史成功记录。

## 其他硬编码审计

- 提醒的表、重试、QQ 发送、Cron 路由、提示词：已归提醒插件。
- bothouse 专用完成判断、文件保存回执判断：核心改用可选 execution v1 数据。
- bothouse 按核心名称取人格对象：改为 message.compose 能力发现。
- 项目工具读取 bothouse 私有配置及残余工作台：已移除。
- 指定第三方插件名称的启动黑名单、跨插件工具描述改写：已移除。
- AstrBot 原生知识库、Skills、MCP、子代理和未来任务管理：保留宿主适配，不属于外部插件业务。
- 核心自身部署时的名称校验：保留，防止正在运行的核心卸载自身任务。
