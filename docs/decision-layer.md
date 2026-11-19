# 决策接口与离线评测

决策服务为 `services/decisions.py`，记录为 `persistence/decisions.py`。宿主仍执行主聊天 agent 阶段，同会话串行；本层只提供路由与关联建议，创建任务、追加输入及执行工具仍经既有权限入口。任务提交返回后不等待后台执行。

## 输入、规则与输出

输入必须引用已持久接收的 `input_events.id`。服务从该引用读取原文、会话和提交者，不接受分类模型提供的身份。程序召回同会话且属于创建者的任务、本人可用的跨会话原文引用、当前群内人物和可用工具。停止、去重、已有任务绑定以及群黑名单、机器人限频、明确在和别人说话、群聊插话预算由规则优先决定；这些路径不调用决策模型。`decisions` 的 `rule_direct` 和最近 14 天直达比例可观测，未预设或承诺线上比例。

输出字段：

| 字段 | 值 |
| --- | --- |
| intent | chat / query / task / supplement / stop / authorization / event |
| route | reply / create_task / attach_task / ask / silent / stop |
| conversation | 必须等于输入的 session |
| topic | 候选原始消息标识或 null；当前实现用于跨会话话题关联，不新造话题实体 |
| task | 同会话、同创建者的候选任务标识或 null |
| evidence | 必含当前 event.id 原值，其余仅任务／话题／人物候选的 id 原值 |
| capability_candidates | 可用能力名称的有序子集 |
| notify | silent / merge / normal / action |
| confidence | 0–1；仅记录、评测，不授予权限 |

程序严格校验字段、类型、身份及关联约束。2026-11-15：与权限无关的格式偏差在 services 中收窄规范化：从 evidence 丢弃非输入／候选 ID，并补入当前 event.id；保持最多 32 项且当前输入引用必保留。能力列表只保留原有候选名称，不补选能力；静默路由的通知降为 silent。空 evidence 可以补入当前输入，但缺失字段、错误类型、超长列表、额外权限字段仍拒绝。不解析带前缀的引用或从描述猜测 ID。

覆盖会话、未知任务／话题、改变固定任务关联、无权限创建／追加任务、模型选择 stop、追加不可接收输入的任务、补充或已关联任务却新建任务、私聊／直呼被静默，均继续拒绝并回退规则基础决策。`stop` 路由只能由规则生成。任务补充不得新建任务，追加前再核对创建者、会话和当前任务状态；入口拒绝偏离本轮已确认关联或已有输入绑定的目标。主人格可以省略 append_input 的 task_id，程序只使用本轮已选目标；无目标时要求澄清。

分类说明明确各字段取 candidates 中的 id／name 原值、evidence 必含 event.id；无任务／话题关联填 null，无能力填 []，无候选的 evidence 仍须包含当前输入。说明不提供可被照抄的示例 ID。旧代码取证未出现代码围栏，但首次修复后真实探测有 2/32 次输出带 json 围栏，因此增加唯一 JSON 对象提取；多对象、损坏的对象、错误结构仍拒绝，提取后沿用全部身份与权限复核。

`decision_enabled` 默认开启，后台“决策”页可关闭。关闭后不调用独立判断模型，也不另发一次主模型分类请求；通过原参与规则的消息直接进入主对话，由主 LLM 结合历史判断意图、回复和派工。记录标为 `main_llm / decision_disabled`，不记作模型故障。黑名单、指向他人的对话、插话预算、重复输入、停止权限和已有任务绑定继续由规则处理。非必要后台进展直接静默，必要结果照常进入主人格表达。设置变更不会中断已经开始的模型调用。

开启时只选一个后端：默认 `decision_backend=haiku`、`decision_provider=shrink/claude-haiku-5-5`。`adapters/decision_backends.py` 调用宿主 provider，默认时限 `decision_timeout=3` 秒，可配置 0.01–10 秒；决策计入 turn_traces 的独立 `decision` 阶段。没有额外的决策自动重试循环，也不会故障后再串行调用另一个分类器。

排查未回复时按日志中的 `input`、`session` 和 `run` 关联 `Shrink input`、`decision`、`history`、`context`、`turn`：分别记录接收身份、参与及静默原因、历史数量与压缩检查点、实际请求体积和最终结局。`system_prefix_same` 只表示系统提示相同，不代表上游缓存命中。后台表达日志 `expression_request`、`expression_failure`、`expression` 记录原会话、材料体积、异常类别、尝试次数及 `plugin_fallback` 等最终路径；`ready` 仅表示表达完成，发送结果仍以通知回执为准。新增日志不打印聊天正文、模型完整请求或凭据。

超时、格式不符或调用失败：私聊和明确叫到石英的消息继续主人格，非直接群聊保持静默。石英直接回复或主动插话后，原发言人可自然接话；其他人插入或石英已经接过一次，就不再续期，仍可点名、引用她或按普通群聊参与。已经实际回复的输入不重复回复。停止目标不明时只询问目标，本轮移除执行工具；明确撤销授权仍绕过防抖，沿用现有停止入口。

分类输入包含参与状态和最多 6 条、每条 240 字、十分钟内的同会话历史，按完整会话身份隔离并排除撤回、已纠正原文和工具回执。接续聊天与主动插话允许自然闲聊，不以明确问题、@或任务需求为前提。主动插话概率用于选择候选；五分钟发言配额在实际调用平台发送前原子占用，同一轮多气泡只计一次。分类静默和主模型跳过不占配额；平台明确拒绝且该轮尚无成功发送时释放，发送结果未知时保守保留至窗口过期。

## 既有规则的去向

| 旧路径 | 本轮行为 |
| --- | --- |
| Participation 的私聊、直呼、黑名单、机器人限制、称呼对象与跟聊对象 | 保留身份及参与范围规则；分类器不能覆盖黑名单或指向他人的会话 |
| 主动插话概率、频率、兴趣加权 | 保留为模型前的参与预算；模型不能扩大已耗尽的预算 |
| engaged / spontaneous 的最终参与 | 经统一决策复核；分类失败按私聊／直呼／群聊三种情况降级 |
| RecentInteractions.ASKED 关键词 | 聊天及该轮 lookup 使用已复核的 topic 关联；分类失败或没有当前决策的后台查询保留关键词降级 |
| RecentInteractions.PRIVATE、本人的来源范围 | 始终保留为权限边界；模型话题、查询词和任务要求不能放宽私聊范围 |
| turn_control.is_task_followup | 优先使用决策的 task / supplement；失败或无决策时保留原关键词 |
| append_input 显式 task_id | 经统一服务复核，与本轮关联、已有绑定和实际所有者一致；TaskInputs 再检查终态和持久去重 |
| 前台 shell、Python、长会话及 transfer_to_* 筛除 | 保留，不因候选发现恢复前台长执行 |

到达钩子使用宿主当前注册且启用的目录；宿主构造好请求后，再用实际挂载、已筛除长执行的工具集复核候选。后一步只做程序排序，不再调用决策模型。分类请求只带最多 4000 字输入、每个任务 500 字摘要、每项能力 300 字描述和最多候选上限三倍的能力；原文仍完整留在收件箱引用的 messages 中，截断不代表内容不存在。默认给主人格最多 5 个候选，配置项 `decision_capability_limit`，范围 1–20。实际工具集合不因候选截断删除；未命中时仍允许使用挂载工具或既有目录发现入口。人格、会话过滤导致某能力未挂载时，不把初选项继续推荐给该轮。

## Laya 接口未核实

仓库与配置未找到 Laya 的部署或接口定义。`docs/local-judge-model.md` 描述的是 Qwen3.5-2B 经临时 llama.cpp 的旧实验，不能据此认定 Laya 的协议或效果。

本轮提供可配置 HTTP 端点适配，默认不选择 Laya。配置 `decision_laya_endpoint`、`decision_laya_version`，显式选择 `decision_backend=laya` 后启用。约定向端点 POST：

```json
{"input": {"event": {}, "candidates": {}, "rules": {}}, "schema": {}}
```

端点须直接返回上表的 JSON 对象；这里的 schema 是 `Decision.model_json_schema()`。这是一份待接入端点实现的契约，**接口未核实**。Haiku 分数记为 `self_report`，Laya 分数记为 `probability`，规则为 `rule`；不跨后端比较原始分数，也未据此设置通用阈值。

## 记录与后台

`GET /api/v1/decisions` 沿用后台鉴权，提供分页记录与直达比例；后台“决策记录”页展示后端、版本、候选、路由、依据、降级、耗时和分数类型。记录保留 14 天，新增记录时清理；输入原文仍由 messages 保存，清理决策记录不删除原文。Haiku 的版本记录是配置 provider 标识，不能代表上游实际模型权重版本。

## 离线 JSONL

每行一个标注样本，`input` 是分类器输入，`context` 是程序规则事实，`expected` 是人工标注。样本不写入应用数据库。最小示例：

```json
{"input":{"event":{"id":"sample1","session":"room","sender":"100","source":"platform","text":"石英，查天气"},"candidates":{"tasks":[],"topics":[],"people":[{"id":"100"}],"capabilities":[{"name":"weather","description":"查天气"}]}},"context":{"direct":true},"expected":{"participation":true,"association":{"conversation":"room","topic":null,"task":null},"capabilities":["weather"],"notify":"normal"}}
```

工具项使用 `name`，其他候选使用 `id`；任务另有 `status`、`request`。可选 context 字段对应 `DecisionContext`（private、direct、admin、participation、foreground、fixed_task）。人工标签与模型请求分离。

脚本对每个后端独立运行同一组输入，调用真实适配边界并复用结构／规则校验，不写数据库。四项准确率分别为：是否参与、conversation/topic/task 三项完全一致、工具名称集合完全一致、notify 完全一致。异常计入失败和四项错误；时延含失败调用，输出 p50／p95／max 秒数。规则直达比例由在线记录提供，离线后端对照不跳过规则可判样本，避免仅比较不同的困难子集。

合成自测，无网络请求：

```powershell
uv run -q --no-project --with-requirements requirements.txt python tools/evaluate_decisions.py tests/fixtures/decisions.jsonl --fixtures tests/fixtures/decision_responses.json
```

真实评测需显式提供端点；Haiku 使用兼容 OpenAI Chat Completions 的完整 URL，访问令牌可通过 `DECISION_EVAL_API_KEY` 环境变量提供，Laya 使用上述自定义契约。两个后端分别评测，不进入线上双模型链路：

```powershell
python tools/evaluate_decisions.py samples.jsonl --haiku-endpoint $env:HAIKU_EVAL_ENDPOINT --haiku-model $env:HAIKU_EVAL_MODEL --laya-endpoint $env:LAYA_EVAL_ENDPOINT --laya-version $env:LAYA_EVAL_VERSION
```

离线响应夹具仅用于验证评测工具，不代表真实模型效果。2026-11-15 的真实 Haiku 合成取证及修复后探测记录在 `runtime/release/report.md`；未确认 Laya 端点，也未测得两模型的对照准确率或分数校准。
