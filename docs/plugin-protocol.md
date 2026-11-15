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
| message.notify | 石英核心 | 持久通知提交、状态、明确未发送的补发及表达前撤回 |
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

授权策略保存在核心，设备访问权由电脑插件判断。`computer.control devices` 行有 access 字段时，只接受 owner／delegated；没有 access 时，该设备出现在对应管理员的列表且未 revoked 就表示可访问。独立插件按 owner 过滤列表，目前没有 grant／ungrant；只读客户端仓库中的插件副本才支持委托。模型和后台调用只能使用实际目录提供的操作，不能根据副本假定独立插件也支持。

`approve` 参数沿用 device_id/session_id/approval_id/allow/request_id，`result` 支持 device_id/request_id 或 command_id。插件 result 返回 `{command_id,state,request,result}`，state 为 pending／completed；request 是数据库保存的 action、arguments（去掉 authorization），result 未完成时为 null。核心先预占审批 request_id，再调用 approve 一次；结果未知只查询原 request_id。确认须满足 state=completed、request.action=approve、原 approval_id／session_id／allow 全部匹配、result.ok=true，并通过有效期余量检查。客户端 `{ok:true}` 不再被误判为缺少对象标识，null 不当作失败。拒绝同样发送 allow=false，并记录具体答复和回执。

approve 的 authorization 携带 policy_id、version、confirmation_id、expires；插件另外保存原 request_id 和 device_id。首次下发、重连补发、心跳补发均调用 `authorization.confirmation validate`，提交 confirmation_id、request_id、device、execution_session、approval_id、allow。核心核对对象、当前访问权、策略版本、撤销、任务及有效期；插件发送前再次检查本地访问权与到期余量。authorization 只在插件保存，设备线上参数不含它。

校验不可用时保持待核对，不发命令。校验明确拒绝时，未下发的命令保存 result.state=not_executed，终止后续补发；已有下发记录的命令保存 unknown，不谎称未执行。重连沿用同一 command_id 和去掉元数据后的相同请求，客户端 Journal 按该标识幂等返回执行中／已完成结果。核心不实现 Codex、Claude 或 DSH 的批准协议。

已对照本机独立插件 main.py／transport.py 和只读 Windows 客户端 AgentSession.cs、Controller.cs、Journal.cs，并以真实插件代码加模拟 WebSocket 设备验证。确认事件 `{kind,session_id,native_id,state,detail:{approval_id,expires,request}}` 由插件转换：expires 必须带时区，ISO-8601 转 Unix 秒；summary 由类别生成短句，不复制原始命令或请求正文。成功提交 authorization.confirmation 后不再走 compose；核心不可用时事件仍为 pending_submit。原始事件留在插件 events 表。

分类使用协议字段：带 method 的 Codex commandExecution/fileChange requestApproval 分别对应 execute/write；Claude can_use_tool 的 Read/Glob/Grep/LS 对应 read，Bash 对应 execute，Write/Edit/MultiEdit/NotebookEdit 对应 write；DSH request_permission 的 toolCall.kind=read/edit/execute 对应 read/write/execute。其他字段或工具为 unknown，永不命中持续授权。当前 AgentSession.Publish 丢弃原 method 和 agent，只发 params/request：Claude subtype/tool_name 与 DSH options/toolCall.kind 可保留分类依据；无法可靠识别的 Codex params 归 unknown，不能靠 command 文本猜协议或权限。核心任务 ID 未包含在客户端事件中，独立插件提交时 task 为空，默认范围仍受设备与具体执行会话限制，不凭会话活动猜核心任务。

客户端已知限制：过期时会把 allow 静默改为 false，仍返回 ok:true，回执不含实际生效的 allow。核心在预占前和 validate 时、插件在发送前，均要求 expires 距当前时间超过 **30 秒**；核心核对回执时也要求同样余量。临界或延迟到期后才查到的 ok:true 保持 unknown，不宣称允许已生效、不生成新审批操作。此保守策略不能证明客户端时钟同步或网络耗时有上界，需真实 Windows 客户端联调；真实 QQ／其他平台也尚未验证。

主人格工具 `shrink_authorization` 支持 list/grant/pause/revoke/answer/reconcile；后台 `/api/v1/authorizations` 与 `/authorizations/action` 使用同一服务。显式 `授权 设备ID 执行会话ID read,execute`（可追加 `未来同类任务`）仍有效。自然答复支持明确的同意／允许／批准／可以／好的／没问题及不同意／不允许／不批准／不要执行／拒绝；附带条件、疑问或未识别修饰时要求澄清，不猜授权。

同会话、同管理员只有一个待确认对象时，answer 可不传标识；有多个时，原话须唯一点明 task／approval_id／execution_session，或引用已接受的具体提问。引用通过可信平台 Reply 的文本与原会话已接受通知精确匹配；引用合并了多个对象的问题仍不能批到其中任意一个。同一局部 ID 不唯一时要求更多标识或管理页选择。管理页同意／拒绝保存精确 confirmation_id、task、allow。

同意之外原话明确“以后这类不用问了”时，由程序从该确认对象建立同设备、同管理员、同任务／执行会话、同类别的策略；只有“以后同类任务不用再问”等明确未来任务的表述才扩大到该设备该类别的未来任务。delete_data 和 grant_permission 必须额外点名删除数据／新增权限，unknown 不能建立策略。不满足持续授权条件仍只逐次同意，返回“未建立持续授权”的说明；模型传入的范围不具有授权效力。后台同构体、工具输出、网页、记忆或模型转述不能成为授权来源。自动批准默认安静；缺少授权交原会话合并询问，撤销／过期／设备不匹配保留现场。


## 统一通知契约与发送所有权

新增 `message.notify` v1：

- `submit` 参数为 event_id（稳定原事件标识）、message_type（稳定事件类别）、facts（对象或字符串）；可选 kind、task_id、fact_refs、fallback_text。返回 id、event_id、state、reason、session、fallback_accepted，表示持久接收或抑制，不等待表达或发送，不把 pending 宣称为已送达。重复 submit 返回原记录，不修改原事实或兜底，也不触发补发。
- `status` 使用相同 event_id、message_type 与原 scope；任务通知可附 task_id，由核心查固定原会话。只有 sent／底层 platform_accepted 表示发送调用已确认成功；unknown 必须待核对。
- `retry` 使用相同 event_id、message_type 与原 scope，可附 task_id 定位，仍要求原会话／原提交者。仅确认未发出的 failed，以及没有活跃执行者且底层为 interrupted／composing 的中断项可以重新排队。复用原记录、事实、事实引用和兜底，不重新提交业务。已有 sent／platform_accepted、sending／unknown、silent／suppressed、cancelled 或活跃表达一律拒绝。返回当前记录及 applied 布尔值，False 表示未改变排队状态。失败的重复 submit 仍不会补发；明确已开始发送后的异常属于 unknown，retry 不会重发。
- `cancel` 参数和范围同 retry，仅 pending／merging 且尚未产生表达回执的通知可转为 cancelled 终态。已经表达过（包括表达后 deferred 再排队）、正在表达／发送或已有回执的拒绝；返回当前状态及 applied=False。cancelled 不再进入泵，重复 submit／retry 不复活；取消不会修改原任务业务状态。
- `fallback_text` 是插件另行提供的可读短句，最长 1000 字符，不能传 facts、事件 body、工具 JSON、异常全文或提示词。接收时按后台通知严格外露规则校验，失败只丢弃该段文本，通知照常接收并返回 fallback_accepted=False。校验通过后存入 notification_outbox.fallback_text；已有表补同名空字符串默认列，与新记录使用相同查询，无旧数据标记。只对必要结果，在既有表达尝试失败后使用；正常表达成功和进展／安静类别不使用。表达前复核人格原行，发送仍经过原有事实、所有权及去重 preflight；不增加发送入口。
- 设置 `notification_max_delay` 默认 60 秒，可设 0–86400 秒。从原通知 created 计时，必要结果超过该时间后不再因为同会话前台活跃或待处理聊天输入而让路；仍受表达资源、模型及发送耗时约束，不能解释成送达 SLA。进展仍可合并或静默，紧急类别原有优先级不变。发送继续在宿主会话锁之外。
- scope 的 session、sender 由可信插件生成。任务通知 facts 必须包含已持久化的 status；核心核对创建者，从任务记录重新取得原会话。群里的当前活跃会话不能覆盖私聊创建任务的回传目的地。
- kind 使用 result/task_result/deployment/question/blocker/cancelled/revocation、progress/tool_progress，或安静类别 tool_start/tool_end/file_read/log/auto_approval/recoverable_error/still_running/internal_handoff。message_type 是事件业务类别，不是平台或模型猜测。
- 同一核心任务由前台、父任务与设备监工回报时传同一个 task_id，核心从持久状态构造事实引用；非核心任务传共同的 fact_refs。同一事实不能让三条通道分别生成无法关联的标识。不能靠文本相似度猜去重。

兼容 `message.compose compose/record_sent` 的参数与 ready/silent 语义不变，compose 仍为只读声明。迁移时两种入口都传相同 message_type；未传的旧 compose 使用 kind（默认 result）作为类别。每个原会话、创建者、消息类型首次选定 core 或 compatibility 发送所有者并持久化；稳定事件标识还有独立的所有权约束，换类别不能让同一事件换发送者。兼容事件先占有时，核心提交保留为 suppressed/compatibility_owner；核心先占有时，compose 返回 silent，record_sent 拒绝兼容路径再记录。没有自动接管或后台热切换旧类型的逻辑；切换已有类别前须停用旧发送者、核清其未决回执并进行明确的维护迁移，不能换消息类型或换事件 ID 绕过所有权。

外部插件一旦采用 submit，必须停止本类事件的自行发送和 record_sent。未迁移插件仍自行持久化发送回执，并在平台接受后调用 record_sent；可带同一个 event_id 和 message_type。协议约束不代替插件自己的持久幂等实现。

本轮已修改独立电脑插件：result/error/disconnected 与监工通知优先 message.notify，事件 ID 分别为 desktop:设备:序号、watch:投递标识；错误与断线使用 blocker。目录明确没有 message.notify submit 时才选旧 compose，目录读取失败不能触发兼容回退。首次接收的新事件将 core／compatibility 所有者写入 notification_submissions，后续核心失败、卸载或重新出现都不切换该事件的所有者。新接收流分别使用 desktop.core.<kind>／desktop.legacy.<kind>；这是区分新流，不是改变已有事件身份。旧 deliveries 的 platform_accepted／unknown／sending 残留不重提，旧 reply_records 只补写已发送记录。未决兼容事件继续 compose，silent／空值／异常一律不发原始资料；发送调用开始后未知不重发。核心接管的事件不再调用插件 send_message 或 record_sent。

真实插件加模拟设备已验证这些发送所有权与失败路径，尚未操作线上所有权数据。2026-11-15：同级独立提醒插件也已接入并联调，迁移与测试说明如下。

提醒插件固定 `event_id=reminder:<投递ID>`、`message_type=reminder.due`，可信 scope 来自其投递记录。submit 带提醒事实及“提醒你：事项。”兜底；核心 failed 后按插件既有 1／5／15 分钟、其后每小时时间表调用 retry；取消已接收通知调用 cancel，按实际返回状态保存。目录没有 retry／cancel 的旧核心保持原 status 等待核对，不调用不支持的操作，也不回退自行发送。原兼容投递继续使用 message_type=reminder，不热切换已尝试事件所有者。

提醒表迁移仅将已尝试的投递（sent／sending／unknown／retry 或 attempts>0）归兼容路径；从未尝试的首次处理时再选择所有者。此前误固定的未尝试记录，仅在 attempts=0、无 last_attempt／文本／消息 ID／核心接收状态且没有已尝试状态时解除兼容所有权。保留原 ID、到期和重试时间、尝试与回执；使用同一字段与查询，不设旧数据标记。

`tests/test_reminder_plugin_integration.py` 直接加载真实同级 `astrbot_plugin_shirnk_ext_reminder` 代码、真实协议客户端、核心 PublicAPI 与发件箱，使用一次性数据库、脚本表达模型和假平台。可用 `SHRINK_REMINDER_PLUGIN_ROOT` 指定路径；缺目录时明确 skipped。本机 8 项全部实际执行：到期私聊／群聊仅发送一次并回原会话、持续前台推迟上限、表达失败后保留事项、明确未开始发送的失败经 retry 恢复、cancel 撤回、unknown 不重发、双方数据库重新打开恢复。发送调用开始后的失败没有通用的“确认未发送”证据，按 unknown 保留；测试故障注入在 compose 边界模拟确认未发送失败。未使用真实模型、真实 Cron 调度器或 QQ。

表达与发送由核心持久发件箱驱动，发送实现仍是原 Notifications.send_once。停止、撤权与阻碍走高优先级通道；普通后台结果在该会话新输入／前台轮次结束后发送，必要结果达到推迟上限可先发，表达结束仍复核一次。进展按任务合并；未知或不可用的通知决策默认静默。原事实通过 input_events 引用 messages，合并结果另存为派生事实。待发项重启恢复；已接受、silent、sending、unknown、cancelled 不重发。发送未知没有跨平台通用查询接口，本轮保留现场供核对，不把重启当成重发授权。

联调测试位于 `tests/test_desktop_plugin_integration.py`，直接加载同级 `astrbot_plugin_shrink_desktop/main.py` 和其 transport；可用 `SHRINK_DESKTOP_PLUGIN_ROOT` 指定其他路径。目录缺失时明确 skipped，本机验证时目录存在、7 项均实际执行。测试通过 aiohttp TestServer/WebSocket 模拟设备，按已检查的客户端 Journal 行为缓存相同 command_id 的结果，不启动真实 C# 程序；aiohttp 与独立插件使用同一依赖范围。核心两套完整命令以及插件自身测试结果记录在计划第 12 节。

核心模型工具不能转发 authorization.confirmation 或 message.notify 伪造可信插件事件，也不能直接 computer.control approve；用户批准走 shrink_authorization 的具体绑定。独立插件的 shrink_computer 在目录存在 authorization.confirmation 时同样拒绝模型直接 approve。已鉴权后台电脑页原有的人工批准转发保留，它不产生持续策略。
