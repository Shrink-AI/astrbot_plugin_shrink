# 石英

石英是一个 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件，用来在 QQ 群里扮演一个普通群友。

普通群聊机器人有几个常见毛病：每条消息都回，一开口就是一大段，过两天就不记得谁是谁，帮人办件事还要汇报一遍内部参数。石英针对这几点做了处理：

- 被叫到才回，偶尔主动插一句。
- 回复通常只有几个字。
- 记得群里的人和聊过的事，每条记忆都带着原话出处。
- 用工具办完事，像同事那样随口交代一句结果。

管理员在网页后台里查看和纠正它记住的东西，也可以修改它用到的每一段提示词。

- 需要 AstrBot 4.29.3 或更高版本
- 主要在 QQ（OneBot / aiocqhttp）上使用和测试
- 许可证：AGPL-3.0

## 能做什么

**聊天**

- 被 @、被引用、被叫名字时回复。刚和某人聊过的话，对方接着说也会接。其他时候按概率插话，话题和它知道的人或黑话有关时概率翻倍。别人互相说话时不插嘴。
- 每次随机选一种回复长度（极短、短句、正常、只发表情），别人要详细内容时才展开。发出去之前会清掉句号、旁白和推理痕迹。
- 同一个人连发几条，会合并成一次回复。正在写回复时对方又补了一句，就丢掉旧回复，两句一起回。
- 表情包和文字二选一：短的反应直接发表情；有内容的话只发字。
- 从群聊里学说话风格和常用说法。风格变化要有聊天记录作依据，可以在后台回滚。

**记忆**

- 以 QQ 号认人。记录称呼、印象、经历和聊过的话题，跨群汇总。
- 聊完一段，后台整理成摘要、话题、记忆和黑话。每条记忆都要附上从原消息里逐字摘出的依据，再让模型逐条核对一遍。拿不准的只作为候选，不会用在回复里。
- 黑话会联网查证释义，查不到的标为推测。
- 记忆和聊天摘要同步到 AstrBot 原生知识库（“石英记忆”“石英聊天摘要”），检索走原生的向量检索。如果 AstrBot 里配了 id 为 `local_bge_reranker` 的重排模型，会再重排一次；没配或超时就按原顺序。

**办事**

- 回复时可以翻聊天记录、查人查事、找表情、在沙箱里跑 Python，也能用 AstrBot 里其他插件和 MCP 提供的工具。
- 每次用工具办事都会复盘：办得好的做法记成“经验”，下次遇到类似的请求时带上。经验同时发布成 AstrBot 原生 Skill，两边的修改会互相同步。
- 管理员交代的长任务会交给 AstrBot 原生子代理在后台执行。主对话不用一直等着，做完再回来汇报结果。
- 管理员可以让它写或改 AstrBot 插件：先在隔离容器里跑测试，通过后才部署，部署失败自动回滚。
- 提醒走 AstrBot 原生定时任务，发送结果有记录。进程重启错过的提醒会在 24 小时内补发，已经发出去的不会重复发。

**后台**

人物、记忆、经验、表情、人格、提示词、整理记录都能在网页里查看、纠正和撤销。每次后台写入都有记录，可以整次撤销。

## 不做什么

- 不替代 AstrBot 的人格设定。石英会把自己的人设同步成 AstrBot 的一个人格，回复时清空宿主的会话历史，由自己管理上下文。
- 不自动保存群里的普通文件、语音和视频。图片和表情包会存一份副本，因为 QQ 的图片链接会过期。
- 写代码、改插件、管理 AstrBot、导入资料这几类工具只有管理员能用。
- 提示词全是中文写的，没有针对其他语言调过。

## 安装

1. 把本仓库放到 AstrBot 的插件目录 `data/plugins/astrbot_plugin_shrink`，或者在 AstrBot WebUI 里按仓库地址安装。
2. 安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

   用官方 Docker 镜像的话，可以参考仓库里的 `Dockerfile` 构建一个带依赖的镜像。

3. 在 AstrBot 里关闭 `provider_ltm_settings.group_icl_enable`。石英自己管理群聊上下文，不关的话同一段聊天会出现两次。
4. 检查其他聊天和记忆插件的会话启用范围，避免同一会话重复回复或重复记录。石英不按插件名称阻止其他插件启动。
5. 重启 AstrBot，打开后台，在设置页选好对话模型和向量模型。

想让它跑 Python 或测试插件，还需要单独起一个沙箱容器，见下面的 [Python 沙箱](#python-沙箱)。

## 后台

后台在容器内的 6186 端口，只接受以 `127.0.0.1` 或 `localhost` 访问。服务器上部署时，用 SSH 转发：

```bash
ssh -L 6186:127.0.0.1:6186 <你的服务器>
```

然后打开 <http://127.0.0.1:6186>。第一次启动会生成管理员密码，放在 `data/plugin_data/astrbot_plugin_shrink/initial-password.txt`，登录并改密码后这个文件会被删掉。

左侧菜单分四组：

| 分组 | 页面 |
| --- | --- |
| （顶部） | 概览：数据量、运行状态、待确认的数据 |
| 群聊 | 聊天与图片、人物、记忆、表情 |
| 说话方式 | 人格、提示词、定向学习、经验 |
| 后台 | 整理记录、开发任务、插件与能力、设置 |

“定向学习”可以直接跟模型说“小明是测试成员甲的另一个称呼”这类话。模型把它拆成具体的修改建议，你确认后才会写入。

## 提示词

石英发给模型的说明，以及几句固定回复，都可以在后台“提示词”页修改，一共一百段左右，按用途分组：聊天回复、记忆整理、人物与群资料、风格与表情、经验复盘、定向学习、后台任务与提醒、工具说明、工具返回、固定回复，以及一条各处共用的“资料不是指令”提示。

- 默认内容在 `shrink/prompts/defaults.toml`，改过的版本存在数据库里，保存后立即生效，不用重启。
- `{{speaker}}` 这样的占位符由程序填入实际内容。保存时会检查：删掉了必需的占位符，或者加了程序不认识的，都会拒绝保存。
- 每次修改都有记录，可以把旧版本放回编辑框，也可以一键恢复默认。
- 改聊天相关的提示词时可以先试一下：选一个群，用石英最近回过的几条消息，按当时的聊天记录分别用现在的版本和你改的版本重新生成回复，并排对比。试的结果不发到群里，也不保存。
- 改人设会同步到 AstrBot 的人格。
- 工具说明就是 AstrBot 交给模型的工具描述。第一段写工具做什么，“参数：”下面每行写“参数名：说明”。参数名和参数个数不能改，参数类型写在代码里。保存后马上推送到已注册的工具上。

注意：有些提示词规定了模型输出的 JSON 格式，程序按字段名解析。页面上会标出来，这类提示词改措辞没问题，字段名别动。

## 开发与接入边界

模块职责、依赖方向和可选插件结果协议见 [架构约定](docs/architecture.md)。核心通过 AstrBot 的工具注册表使用其他插件，不导入它们的实现；接入插件可以独立启停。工作台、子代理和记忆由核心协调，站点与凭据归接入插件。运行 `python -m pytest tests -q` 会同时检查这些边界。

## 设置

都在后台设置页，常用的几项：

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `model_provider` | | 对话和后台整理用的模型 |
| `embedding_provider` | | 记忆检索用的向量模型，也用来建知识库 |
| `spontaneous_probability` | 0.06 | 没人叫它时插话的概率，话题相关时翻倍 |
| `spontaneous_limit` | 2 | 每个群五分钟内最多主动插话几次 |
| `engaged_seconds` | 180 | 回复过某人之后，这段时间内对方继续说也会接 |
| `debounce_seconds` | 1.5 | 同一个人连发消息时，等多久没新消息才开始回 |
| `collect_groups` | 空 | 收集表情的群号，只有这些群的图片会进表情库 |
| `context_budget` | 4000 | 回复时带上的群聊记录字数上限 |
| `split_probability` | 0.15 | 一条回复拆成两条发的概率 |
| `sandbox_url` | `http://shrink-sandbox:8765` | Python 沙箱地址 |

## 一条消息是怎么处理的

| 步骤 | 代码 | 做什么 |
| --- | --- | --- |
| 感知 | `services/perception.py` | 把 AstrBot 事件变成 `Turn`：谁说的、@ 了谁、引用了什么、这句是对谁说的 |
| 决定 | `services/participation.py` | 回不回、算不算接着聊、能不能插话；其他机器人叫它，两分钟内最多回一次 |
| 合并 | `services/debounce.py` | 合并同一个人的连发消息；对方在它回复前又说了话，就丢掉旧回复一起回 |
| 上下文 | `services/context.py` | 近期原文、更早的摘要、按关键词找回的旧话题 |
| 回忆 | `services/memory.py`、`services/people.py` | 人物卡片、称呼、记忆、黑话，多路召回后打分，低于阈值不用 |
| 表达 | `services/expression.py`、`services/reply_text.py` | 选回复长度，清洗输出，太长或和最近说过的话重复就改写一遍 |
| 整理 | `services/digest.py`、`glossary.py`、`consolidation.py` | 后台整理聊完的群聊；每天凌晨更新人物和群的小结，清理过期和重复的记忆 |
| 复盘 | `services/skills.py` | 回看用工具办事的过程，写成或修改经验 |

分层约定：`services` 不导入 AstrBot 和 FastAPI，SQL 只写在 `persistence` 里，和 AstrBot 打交道的代码都在 `adapters`，所有提示词经过 `shrink/prompts`。群聊记录、记忆、搜索结果这类资料放进提示词时都用标签包起来，并注明“不是指令”。

## 记忆怎么防脏数据

模型提出的每条记忆、称呼、黑话都要经过 `services/quality.py` 检查：

1. 命令、石英自己说的话、机器人账号说的话、纯链接、超长转发、复读、像注入指令的内容，都不拿来学习。
2. 必须带一段从原消息里逐字摘出的引文，代码会核对原文里确实有这段话。
3. 标注是本人说的、推断的、转述的，还是玩笑。玩笑不存，推断和转述降低置信度。
4. 记忆的对象只能是说话人本人，或者被 @、被引用、被称呼的人。
5. “明天”“下周”换算成具体日期。出差、考试这类临时状态带有效期，过期不再召回。
6. 置信度不够的只作为候选，不会用在回复里，被别的聊天再次印证后才转正。
7. 同一件事合并，新事实取代旧的。管理员确认过的数据，模型不能改。
8. 有数量上限：每次整理最多 12 条，同一个人每次最多 3 条、每天最多 8 条。

## 工具

回复时模型可以调用：

| 工具 | 作用 | 谁能用 |
| --- | --- | --- |
| `shrink_chat_history` | 按关键词、人、天数翻当前会话的记录 | 所有人 |
| `shrink_chat_attachments` | 找当前会话存下来的图片和表情，读文本附件，或把副本发回来 | 所有人 |
| `shrink_lookup` | 查某个人或某件事的记忆 | 所有人 |
| `shrink_find_meme` | 按描述找一张表情 | 所有人 |
| `shrink_run_python` | 在沙箱里跑 Python | 所有人 |
| `shrink_remember` | 用户明确说“记住”时，存一条带来源的记忆 | 所有人 |
| `shrink_curate_material` | 把交给它的资料核验来源后，按记忆的规则整理进去 | 管理员 |
| `shrink_orchestrate` | 建子代理、拆分任务，交给后台执行 | 管理员 |
| `shrink_develop` | 把写插件、改插件的需求交给 AstrBot 原生开发子代理 | 管理员 |
| `shirnk_plugin_workbench` | 检出、修改、测试、部署、回滚插件代码 | 管理员 |
| `shrink_native_manage` | 通过 AstrBot 自己的管理接口操作插件、MCP、Skill、子代理、知识库、模型和提醒 | 管理员 |
| `shrink_deliver_reminder` | 提醒到期时由定时任务调用，负责发送和记录回执 | 定时任务 |

## Python 沙箱

沙箱是一个单独的容器，复用 AstrBot 镜像，只连内部网络，用来跑 `shrink_run_python` 的代码（10 秒、512 MB、没有外网、只读文件系统，带 numpy），以及开发任务里的插件测试。compose 示例：

```yaml
services:
  shrink-sandbox:
    image: <你的 AstrBot 镜像>
    container_name: shrink-sandbox
    restart: unless-stopped
    user: "65534:65534"
    entrypoint: ["python", "-I", "/sandbox/server.py"]
    read_only: true
    tmpfs: ["/tmp:size=64m,mode=1777"]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 768m
    pids_limit: 64
    cpus: 1.0
    volumes: ["./sandbox:/sandbox:ro"]
    networks: [sandbox]

networks:
  sandbox:
    internal: true
```

把本仓库的 `sandbox/server.py` 放到 `./sandbox/`，让 AstrBot 服务也加入 `sandbox` 网络，再在设置页填好 `sandbox_url`。不配沙箱也能用，只是跑不了代码，也测不了插件。

## 数据

运行数据在 `data/plugin_data/astrbot_plugin_shrink/`：

- `shrink.db`：SQLite 主库，WAL 模式。
- `memes/`：表情图片。

升级时数据库自动迁移。记忆正文同时存在 AstrBot 的知识库里，只备份 `shrink.db` 恢复不全，备份时把 AstrBot 的 `data` 目录一起带上。

## 开发

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest pytest-asyncio httpx ruff
.venv/bin/python -m pytest -q
.venv/bin/ruff check --config pyproject.toml shrink tests tools main.py

cd web && npm ci && npm run build   # 后台前端，产物输出到 static/
```

Windows 上把 `.venv/bin/` 换成 `.venv\Scripts\`。

`tools/` 里的辅助脚本：

- `preview.py --seed --port 6187 --data <目录>`：用一次性数据启动后台，`--seed` 会填入演示内容，不需要 AstrBot。
- `check_dashboard.cjs`：用 Playwright 检查后台各页面在桌面和手机上的布局。
- `integration_probe.py`、`sticker_probe.py`、`runtime_probe.py`、`task_reply_probe.py`：要在 AstrBot 容器里运行，用宿主钩子和模拟消息检查流程与表情发送，不会往 QQ 发消息。`runtime_probe.py` 通过环境变量 `SHRINK_PROBE_MODEL`、`SHRINK_PROBE_EMBEDDING` 指定 `cmd_config.json` 里的提供商 id；模型调用会访问已配置的提供商。
- `bundle.py`：在 Git 工作副本中运行，生成 `runtime/shrink-release.zip`。只打包 Git 可见且未被忽略的文件；包含未提交的新源码，不包含 Git 历史。发布前提交所需改动，并确认未纳入凭据、聊天记录或运行数据。不要直接压缩整个工作目录。

## 独立接入插件

| 仓库 | 职责 |
| --- | --- |
| [shrink-desktop](https://github.com/cneicy/shrink-desktop) | Windows C# 托盘被控端，直接连接本机 Agent |
| [astrbot_plugin_shrink_desktop](https://github.com/cneicy/astrbot_plugin_shrink_desktop) | 设备配对、电脑任务、会话监工和结果回传 |
| [astrbot_plugin_shirnk_tools](https://github.com/cneicy/astrbot_plugin_shirnk_tools) | CraftTable / Gitea 项目工具 |
| [astrbot_plugin_shirnk_ext_reminder](https://github.com/cneicy/astrbot_plugin_shirnk_ext_reminder) | 原生未来任务、提醒回执和补发 |

插件通过公开能力协议协作，分别安装和维护，不随核心仓库打包运行配置。桌面控制使用主动 WSS 连接，不依赖 bothouse。

## 致谢

石英是独立实现的，只用了 AstrBot 公开的插件、人格、提供商、知识库、Skill、子代理和定时任务接口。功能分工参考了 chat_plus、LivingMemory、Self Learning 和 meme_manager，没有复制它们的代码。
