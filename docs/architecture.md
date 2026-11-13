# 石英核心与 AstrBot 集成

## 职责与数据所有权

- `Application` 组合领域服务和本地仓储，不依赖 AstrBot 的生命周期或 HTTP 管理接口。
- `services` 负责聊天判断、资料整理、记忆校验、经验复盘及草稿管理。资料原文不是指令，接入工具的返回值也不能直接写成长期记忆。
- `persistence` 保存人物、来源、确认、版本、撤销、聊天和操作证据。知识库正文的本地字段是兼容投影及待同步编辑，不是第二份独立正文。
- `adapters` 负责 AstrBot 的知识库、原生 Skills、子代理、定时任务及管理接口。`PluginHost` 负责这些适配器的启动与关闭。
- `api` 和工具入口只负责权限、参数转换、调用及结果展示，复用同一业务服务。
- CraftTable、Gitea、bothouse 接入插件只提供项目工具。石英决定是否整理其结果，核心统一保存来源与记忆。

## 依赖方向

```text
main.py → PluginHost（装配与生命周期）
                 ├─ AstrBot 适配器 → services / persistence / ports
                 ├─ HTTP API      → 应用服务接口
                 └─ Application   → services → persistence / ports / prompts
其他插件 → AstrBot 工具注册表 ← 石英发现并调用
```

`services`、`persistence` 和 `api` 不导入 AstrBot 或 `adapters`。宿主事件、插件加载、原生知识库与管理 API 均由适配层处理。`Application` 的宿主能力由 `PluginHost` 装配；HTTP 路由调用已装配接口，不构造 AstrBot 事件。任务上下文位于 `services/task_context.py`，聊天钩子和代理编排不通过相互导入传递任务身份。

工作台负责草稿、路径约束、差异、测试证据、冲突检查及备份恢复；宿主加载与源码读取通过 `AstrBotWorkbenchRuntime` 注入。独立部署执行器可以复制并导入工作台文件，不依赖正在卸载的核心包；使用说明通过回调注入。`Operations` 是现有开发与编排的兼容门面，`AgentTasks` 仍为其内部实现，不是供其他插件继承的公共 API。

草稿与线上冲突时，`refresh` 归档旧草稿、原基线和测试记录，再检出当前线上源码；不部署、不合并旧修改。`archives` 列出归档，`read_archive` 按归档 ID 和相对路径读取旧文件。新草稿必须重新测试，部署仍校验线上基线。提示词 TOML 文件参与检出、差异、隔离测试及版本校验。

## 可选插件边界

- 核心不导入其他插件模块，不要求接入插件安装，也不按其他插件名称拒绝启动。多个插件同时响应聊天时，由 AstrBot 会话启用范围和部署配置管理，核心不替其他插件启停。
- 外部能力只通过 AstrBot 已启用工具发现与调用；插件移除后，工具发现结果变化，核心聊天和资料整理继续可用。缺失工具是该任务的明确阻碍，不能伪造替代结果。
- 核心只修改 `handler_module_path` 属于自身模块的工具描述及参数说明，即使其他插件注册同名工具也不修改其定义。跨插件重名仍应由注册方解决。
- 项目、空间、账号、凭据和站点调用规则归接入插件。核心不解析专属工具名或中文回执，不在领域服务中添加站点分支。历史 `shirnk_plugin_workbench` 名称保留兼容，所有权属于核心。
- 专用运维探针可导入目标接入插件，但必须置于 `tools/`，不能成为核心启动或测试依赖。常规测试不要求安装任何接入插件。

接入插件由各自仓库维护，独立部署、配置和启停，不随核心发布。按 [插件通信协议](plugin-protocol.md) 发布能力，按能力名和版本发现；核心仅提供人格表达与通用发现调用，不持有提醒等插件的业务状态或路由。接入插件不得读取另一插件的配置、访问其 host 或导入 data.plugins 模块。

## 工具结果约定

普通工具结果继续按原样供模型理解。接入方可在 JSON 顶层增加可选的结构化证据，核心用它记录执行状态：

```json
{"result": "工具原有返回", "execution": {"version": 1, "status": "submitted", "task_id": "job-id"}}
```

`status` 支持 `submitted`、`running`、`completed`、`failed`、`unknown`；`task_id` 与 `receipt_id` 可选。核心只提取这些字段，不回显其他未知字段。该约定表示工具自己报告的事实，不是新的权限授予或真实性证明；单步完成不能等同整个任务完成，平台接受不能等同用户已读。

未声明版本、未知版本或纯文本回执不推断成功，超时只说明已调用工具而完成情况未确认。旧提示词键保留以兼容保存过的编辑与历史记录，但专属工具判断不再执行。接入插件可独立逐步采用协议，核心不强制它们升级，也不读取其私有数据库。

## 架构检查

运行 `python -m pytest tests -q`。`tests/test_architecture.py` 固定依赖方向、禁止导入其他插件、验证工作台独立加载及运行时注入；提示词测试验证不会改写外部同名工具。新增功能优先在已有层实现；出现宿主 SDK、平台事件或插件生命周期依赖时放入适配层。

## 写入与恢复

记忆编辑经持久待处理队列同步至原生知识库，读回验证后才替换正文关联。更新失败保留旧知识库版本用于召回；删除和撤销先更新本地元数据，候选检索再按当前关联、状态和会话规则过滤。重排只能作用于已通过规则的候选。

经验保留原有复盘、版本和成败记录，发布为原生 `SKILL.md`。原生编辑回写经验版本，原生删除停用经验，双边修改冲突不能静默覆盖。原生路由标识使用 ASCII；中文标题、用途和步骤保留在文档中。

聊天原文独立记录。仅图片和表情包自动保存副本；普通文件、语音和视频不自动留档。资料整理保留用户明确交付的正文及来源，不自动抓取整个项目。

## 开发执行

石英提交任务，原生开发子代理使用配置的模型与工具。任务、草稿、差异、测试证据和部署结果独立记录。修改后旧测试证据失效；部署同时校验线上版本冲突。

隔离测试容器使用与线上一致的 AstrBot 和依赖，不挂载生产凭据或可写插件目录。部署失败恢复上一版。核心自身重载必须由生命周期独立的部署执行器执行，不能在即将被卸载的协程中把“开始重载”记为完成。

## 验收边界

语法正确、隔离测试通过、插件加载、真实功能运行和消息送达是不同状态。消息送达必须记录平台回执；发送结果未知时先查询记录，不能盲目重发。

接口、配置和模块边界允许破坏性变更。迁移保留来源、人物关联、确认状态与历史关系；修改前备份，升级后的调用方和后台必须使用新接口并完成实际验证。

## 任务状态、执行尝试和通知

任务 SQL、建表和备份在 `persistence/tasks.py`，转换规则与启动恢复在 `services/task_states.py`，调度、执行槽和取消在 `services/task_execution.py`。`Operations`／`AgentTasks` 保留兼容入口及宿主事件、工具、runner 调用，不直接修改任务状态。运行期和迁移都经 `TaskStates.transition`，仓储使用前态比较更新；非法转换报错。任务状态改变与尝试结束／重试时间在同一事务提交。

三类状态分开存放：

| 结构 | 保存内容 |
| --- | --- |
| `operation_tasks` | 目标、创建者、原会话、依赖、业务状态、下一次执行时间、单步重试计数、执行／汇总阶段、停止请求 |
| `task_attempts` | 尝试 ID、执行器、宿主会话引用、检查点引用、开始／结束时间、尝试状态、结局、错误分类、重试序号及起始步骤 |
| `notification_receipts`、`task_delivery` | 真实发送回执和任务通知投影；HTTP 的 `delivery` 字段由关联查询返回，不再存入任务业务表 |

`session_ref` 保存宿主 UMO，`checkpoint_ref` 指向该任务的持久消息上下文。原生 runner 是进程内对象；人工恢复创建同一任务的新尝试，从检查点续做，不声称接管旧进程。操作事件关联尝试 ID；另一次尝试的工具结束不能抵消原尝试的未知写入。完成／确认取消后清理检查点，输入位置、执行记录、尝试和回执仍保留。

业务状态为 `queued`、`running`、`waiting_external`、`waiting_user`、`retry_wait`、`reconciling`、`needs_attention`、`completed`、`failed`、`cancelling`、`cancelled`。主路径见 agent-architecture-plan.md 7.4。实现补充以下调度边：排队任务可进入依赖等待或待处理；依赖／子任务返回后重新排队；外部回执可结束等待；原创建者明确续做可把待处理、失败、取消任务重新排队。已完成任务不能 resume。等待任务可停止；外部执行与核对中的停止也需经过 cancelling，不能假定远端已经停止。所有人工续做重新校验管理员身份、原会话、创建者、12 项上限及单开发任务限制。

### 旧状态映射与恢复

| 旧状态 | 迁移后的业务状态 | 处理依据 |
| --- | --- | --- |
| queued | queued | 无已开始且未结束的尝试即可重新排队；历史已结束尝试不阻止下一次排队 |
| running | 按恢复证据分到 queued／reconciling／needs_attention | 最新尝试未开始则 queued；工具开始未闭合则 reconciling；旧库没有尝试证据时不能假装仍在执行 |
| waiting | waiting_external | 保留等待；启动时若缺少尝试和外部等待依据，转 needs_attention，不盲目重跑 |
| awaiting_input | waiting_user | 保留问题，原用户答复后继续同一任务 |
| interrupted | needs_attention；存在未闭合工具时 reconciling | 不再把“进程结束”当成业务结局 |
| timed_out | needs_attention；存在未闭合工具时 reconciling | 旧库没有可靠单步重试预算，不凭超时文本自动重试 |
| blocked | needs_attention | 保留前置任务阻碍，不伪造已执行 |
| testing | needs_attention；存在未闭合工具时 reconciling | 没有结束证据不能假定测试仍运行或已经通过 |
| deploying | waiting_external（deployment） | 等独立部署执行器的真实结果 |
| awaiting_deployment | waiting_external（deployment） | 同上，部署阶段不作为任务业务状态 |
| continued | cancelled | 旧实现已把后续工作交给另一任务；原记录停止，不推断完成或复制续做任务 |
| completed／failed／cancelled／needs_attention | 保持原状态 | 已完成和用户停止的工作不自动重建 |
| 未识别状态 | needs_attention | 保留事实，等待明确处理 |

迁移前通过 SQLite backup API（包含 WAL 中已提交页）生成数据目录下的 `shrink.before-task-states-v2.db`，成功后才修改任务结构。状态迁移和全库迁移版本键 `operation_task_states_v2` 原子提交；重复启动不覆盖备份、不重复迁移，不给任务增加“旧数据”标记。迁移失败不启动执行器，保留备份供维护端恢复。生产库副本和实际停机回滚仍需上线前验收。

启动恢复不启动未知副作用：未开始的尝试／排队任务回到 queued；未闭合工具调用进入 reconciling；无法判断的在途尝试结束为 interrupted，任务进入 needs_attention；业务 cancelled 保持不变。retry_wait 保留原 deadline，waiting_user 保留待决问题。停止请求也持久保存，进程重启不会自动恢复用户已停止的工作。

### 单步错误与重试

`services/task_errors.py` 是纯函数，接收异常类型／状态码或明确的错误类别，不把日志文字当成执行成功证据。

| 错误类别 | 下一业务状态 |
| --- | --- |
| 网络、连接超时、限流、可恢复服务端错误 | retry_wait；预算耗尽后 needs_attention |
| 参数无效、工具不存在 | needs_attention，修正接口或参数后人工续做，不原样循环 |
| 写入结果未知 | reconciling，保留原操作参数与记录，先核对 |
| 权限拒绝 | waiting_user；不自动重试绕过权限 |
| 输出格式错误／空输出 | retry_wait，仅重做未成功的模型请求；预算耗尽后 needs_attention |
| 长命令仍运行 | waiting_external；保留返回的句柄／事实，不重新提交命令 |
| 发送结果不明 | reconciling（执行错误分类）；通知回执独立保留 sending／unknown，不重发 |
| 持续不可用／无法归类 | needs_attention |

后台 `task_retry_limit` 默认 3，可设 0–3，含义为初次请求之后最多 3 次自动重试。每次成功的安全步骤重置该步骤预算；下次执行时间持久化，退避为 2／4／8 秒。调度器每秒检查到期项，再排队创建新尝试；retry_wait 没有占用执行槽的协程，也不重新执行检查点之前已完成的工具。最多 3 个后台执行槽，最多 12 项活跃代理任务；开发任务仍只允许一项持有工作区。无整项任务墙钟截止时间。

核对基线为已安装 AstrBot 4.29.4 及该测试环境 SDK 源码：

| 层 | 原有次数 | 后台任务处理 |
| --- | --- | --- |
| 石英旧 run_agent | 共 3 次尝试，槽内 sleep | 删除；由持久调度器控制单步预算 |
| 宿主 runner | 空输出共 3 次尝试，另可切换 fallback providers | 任务 runner 单次请求，不走该重试／fallback 包装；保持 full 工具模式，无 skills_like 二次取参 |
| 宿主 request_retry | 默认共 5 次请求 | `request_max_retries=1`，含义是共一次请求，不是额外一次 |
| OpenAI／Responses 提供者 | SDK max_retries=0；提供者外层最多 10 轮错误修复／换 Key | 每个任务使用提供者副本，错误直接上交，不换 Key 原地循环 |
| Anthropic SDK | 默认额外重试 2 次，宿主没有覆盖 | 副本 client.with_options(max_retries=0)，不修改前台共享实例 |
| Gemini 提供者／SDK | 外层最多 10 轮，查询内有格式修复循环；非流式 recitation 额外 2 次；SDK 未配 retry_options 时通常一次，另有隐式流重试分支 | 副本禁止外层循环；每次模型调用只允许一次 query config 准备；每请求显式 SDK attempts=1，防止内层修复及隐式重试相乘 |

真实宿主测试使用脚本模型和假传输验证次数、空输出预算以及已完成写工具不重跑；第三方提供者自身隐藏的重试和真实服务端行为不属于这组已验证范围，接入前需核对。前台聊天和统一表达服务仍沿用各自原有策略。

取消先持久记录 cancelling，再请求执行端停止；确认结束且没有未知工具结果时 cancelled。工具回执未闭合、独立外部执行无法确认、或 5 秒内未收到停止确认时进入 reconciling，保留现场。执行端尚未停止时拒绝 resume；迟到结果不能把被停止的任务标成完成。

### 部署链与接口

独立部署目录中的 queued／running／issued／completed／rolled_back／failed／unknown 是部署执行器状态，不迁入任务状态列。开发任务提交部署后为 waiting_external，wait_reason=deployment；收到完成证据才进入 completed，失败或不明转 needs_attention。收到结果后经同一 transition 入口归并，已停止的任务不复活；部署通知仍独立按真实回执去重，不因业务状态变更重新发送。

`/api/v1/operations/tasks`、详情、cancel、resume、redeliver 路径保持兼容。resume 可带 `{"answer":"原用户答复"}`，保留任务 ID；详情增加 attempts、next_run、retry_count。问题与后续结果使用各自稳定通知标识，避免同一任务的问题回执吞掉最终结果。前端 TaskStatus 使用新状态联合类型，开发任务“完成”和独立部署“完成”分别展示。
