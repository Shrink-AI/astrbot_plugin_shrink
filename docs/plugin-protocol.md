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

`message.compose` 的 `compose` 返回 `{"status":"ready","text":"…"}` 或 `{"status":"silent","reason":"expression_unavailable"}`。`silent` 是成功的静默决定，没有 `text`；调用方必须停止发送，不能将缺少文本视为错误后回退到 facts、事件 body 或日志。外部 facts 始终按执行资料处理，`kind="tool_result"` 不授予原文外发资格。原始资料先保存到核心 `outbound_compositions` 表，保留 14 天，启动及后续写入时清理；主模型聊天答复不在该表重复留底。表达超时或空输出最多重试表达一次，不重跑工具。`compose` 保持 `readonly:true`，内部留底不要求用户逐次确认；`record_sent` 仍是写操作。`record_sent` 仍只记录平台已接受的文本。

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


## 持续授权与确认契约

核心 `authorization.confirmation` v1 提供 `submit/check/validate`。确认事件必须包含 provider、device、execution_session、approval_id、category、expires（Unix 秒），可带 task、summary。身份与原会话来自可信 scope；task 必须属于该创建者和原会话。事件以提供者、设备、执行会话、确认对象的组合去重，重复标识不能改变事实。

授权策略保存在核心，设备访问权仍由电脑插件判断。沿用 `computer.control devices` 返回 id/revoked/access（owner 或 delegated），`approve` 参数 device_id/session_id/approval_id/allow/request_id，`result` 参数 device_id/request_id。核心先持久预占审批 request_id，再调用 approve 一次；中断、超时或未知只查询同一 request_id。确认回执要求 `state=completed` 且 `result` 含 ok=true、allow=true、原 approval_id 与 session_id；仅提交成功不算执行完成。

新增 approve 的 authorization 对象携带 policy_id、version、confirmation_id、expires。电脑插件在排队命令实际执行前必须调用 `authorization.confirmation validate`（confirmation_id、request_id），并重新检查设备访问权；只有 allowed=true 才能执行。撤权入口立即阻断本地策略，旧版本、过期、任务结束与撤销均拒绝执行校验。外部设备访问权在插件内发生变更时，插件同样必须拒绝已排队旧命令。核心不实现 Codex、Claude 或 DSH 的批准协议。

**未与真实插件联调**：本仓库只有现有后台参数和能力调用方，没有电脑插件实现。确认事件推送、操作类别分类、request_id 的持久幂等、批准结果精确结构、执行前 validate 回调及远端撤权时的队列作废均为本轮明确的契约，不能视为真实电脑端已支持。未确认或契约不符的回执保持 unknown，不自动重发。

主人格工具 `shrink_authorization` 支持 list/grant/pause/revoke/answer/reconcile；后台 `/api/v1/authorizations` 与 `/authorizations/action` 使用同一服务。聊天授权采用明确文本 `授权 设备ID 执行会话ID read,execute`，需要未来同类任务时明确追加 `未来同类任务`；模糊的“以后不要再问”需先明确范围。类别为 read/execute/write/delete_data/grant_permission。默认限定原会话与当前任务／执行会话，策略永不授予设备访问权。管理表单可指定管理员、有效期与任务，须与保存的可信原始输入完全一致。

逐次答复必须绑定 task 和 confirmation_id；聊天文本必须明确 `同意 确认对象ID`，同一原会话存在重复局部 ID 时使用管理页逐一选择。裸“同意”、跨任务答复、后台同构体、工具输出或模型转述不能授权。自动批准默认无对外通知；缺少授权的确认对象交原会话合并询问，撤销／过期／设备不匹配保留现场。


## 统一通知契约与发送所有权

新增 `message.notify` v1：

- `submit` 参数为 event_id（稳定原事件标识）、message_type（稳定事件类别）、facts（对象或字符串）；可选 kind、task_id、fact_refs。返回 id、event_id、state、reason、session，表示持久接收或抑制，不等待表达或发送，不把 pending 宣称为已送达。
- `status` 使用相同 event_id、message_type 与原 scope；任务通知可附 task_id，由核心查固定原会话。只有 sent／底层 platform_accepted 表示发送调用已确认成功；unknown 必须待核对。
- scope 的 session、sender 由可信插件生成。任务通知 facts 必须包含已持久化的 status；核心核对创建者，从任务记录重新取得原会话。群里的当前活跃会话不能覆盖私聊创建任务的回传目的地。
- kind 使用 result/task_result/deployment/question/blocker/cancelled/revocation、progress/tool_progress，或安静类别 tool_start/tool_end/file_read/log/auto_approval/recoverable_error/still_running/internal_handoff。message_type 是事件业务类别，不是平台或模型猜测。
- 同一核心任务由前台、父任务与设备监工回报时传同一个 task_id，核心从持久状态构造事实引用；非核心任务传共同的 fact_refs。同一事实不能让三条通道分别生成无法关联的标识。不能靠文本相似度猜去重。

兼容 `message.compose compose/record_sent` 的参数与 ready/silent 语义不变，compose 仍为只读声明。迁移时两种入口都传相同 message_type；未传的旧 compose 使用 kind（默认 result）作为类别。每个原会话、创建者、消息类型首次选定 core 或 compatibility 发送所有者并持久化；稳定事件标识还有独立的所有权约束，换类别不能让同一事件换发送者。兼容事件先占有时，核心提交保留为 suppressed/compatibility_owner；核心先占有时，compose 返回 silent，record_sent 拒绝兼容路径再记录。没有自动接管或后台热切换旧类型的逻辑；切换已有类别前须停用旧发送者、核清其未决回执并进行明确的维护迁移，不能换消息类型或换事件 ID 绕过所有权。

外部插件一旦采用 submit，必须停止本类事件的自行发送和 record_sent。未迁移插件仍自行持久化发送回执，并在平台接受后调用 record_sent；可带同一个 event_id 和 message_type。协议约束阻止核心与兼容路径同时发送，但不代替未迁移插件自己的幂等实现。现有独立提醒、电脑监工插件均不在本仓库，**未与真实插件联调**，本轮没有修改其发送代码或线上所有权。

表达与发送由核心持久发件箱驱动，发送实现仍是原 Notifications.send_once。停止、撤权与阻碍走高优先级通道；普通后台结果在该会话新输入／前台轮次结束后发送，表达结束再复核一次。进展按任务合并；未知或不可用的通知决策默认静默。原事实通过 input_events 引用 messages，合并结果另存为派生事实。待发项重启恢复；已接受、silent、sending、unknown 不重发。发送未知没有跨平台通用查询接口，本轮保留现场供核对，不把重启当成重发授权。

核心模型工具不能转发 authorization.confirmation 或 message.notify 伪造可信插件事件，也不能直接 computer.control approve；用户批准走 shrink_authorization 的具体绑定。已鉴权后台电脑页原有的人工批准转发保留，它不产生持续策略。
