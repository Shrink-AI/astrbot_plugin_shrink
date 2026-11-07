# 石英 / Shrink

[AstrBot](https://github.com/AstrBotDevs/AstrBot) 的群聊人格插件。让 bot 像群里的普通群友一样说话：
记得人和聊过的事，看得懂群里的黑话，不会每句都回，也不会满嘴客服腔。自带一个独立的管理后台。

- 要求 AstrBot 4.29.3 及以上
- 许可证 AGPL-3.0

## 它做什么

- **像人一样参与**：被 @、被引用、被叫名字时回复；其余时候按概率插话，话题相关时概率更高；别人在互相说话时不插嘴。
- **记得人**：以 QQ 号为身份，记录称呼、印象、聊过的事，跨群综合。每条记忆都要有原话依据，拿不准的只作为候选，不会进入回复。
- **懂群里的话**：从聊天里整理摘要、话题和黑话，黑话会联网查证。
- **说话简短**：每次随机选一种回法（极短、短句、正常、只发表情），去掉句号、旁白和推理痕迹，偶尔拆成两条发。
- **会用工具**：翻聊天记录、查人查事、找表情、在沙箱里跑 Python。用工具办成（或办砸）的事会被复盘成“经验”，下次遇到类似的请求带进提示词。
- **后台可管**：人物、记忆、经验、表情、人格、整理记录都能在网页里查看、纠正、撤销。

## 安装

1. 在 AstrBot 插件目录放入本项目（`data/plugins/astrbot_plugin_shrink`），或在 WebUI 里按仓库地址安装。
2. 安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

   用官方 Docker 镜像时，可以参考仓库里的 `Dockerfile` 构建带依赖的镜像。
3. 关闭 AstrBot 的 `provider_ltm_settings.group_icl_enable`。石英自己管理对话上下文，
   回复时会清空宿主的会话历史，不关的话同一段群聊会被注入两遍。
4. 不要和 `astrbot_plugin_group_chat_plus`、`astrbot_plugin_livingmemory`、
   `astrbot_plugin_self_learning` 同时启用，它们功能重叠，检测到会拒绝启动。
5. 重启 AstrBot，在后台设置页选好对话模型和向量模型。

## 后台

后台监听容器内的 6186 端口，只接受 `127.0.0.1` / `localhost` 访问。
远程部署时用 SSH 转发，或把端口只映射到本机回环地址：

```bash
ssh -L 6186:127.0.0.1:6186 <你的服务器>
```

然后打开 <http://127.0.0.1:6186>。首次启动会生成管理员密码，
放在 `data/plugin_data/astrbot_plugin_shrink/initial-password.txt`，登录并修改密码后该文件会被删除。

页面：概览、人物、记忆、定向学习、经验、整理记录、人格、表情、设置。

## 配置

所有设置都在后台设置页，常用的几项：

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `model_provider` | | 对话和整理用的模型提供商 |
| `embedding_provider` | | 记忆检索用的向量模型 |
| `spontaneous_probability` | 0.06 | 普通群聊的插话概率，话题相关时加倍 |
| `spontaneous_limit` | 2 | 每个群五分钟内最多主动插话几次 |
| `collect_groups` | 空 | 开启消息收集的群号列表，只有这些群会被学习和整理 |
| `engaged_seconds` | 180 | 石英回复某人后，这段时间内对方不 @ 继续说话也会接 |
| `context_budget` | 4000 | 群聊窗口的字数预算 |
| `split_probability` | 0.15 | 一条回复拆成两条发的概率 |
| `sandbox_url` | `http://shrink-sandbox:8765` | Python 沙箱地址 |

## 工作方式

一条消息依次经过“感知 → 决定 → 回忆 → 表达 → 反思”：

| 环节 | 位置 | 做什么 |
| --- | --- | --- |
| 感知 | `services/perception.py` | 把事件变成 `Turn`：说话人、@、引用、图片，以及这句是对谁说的 |
| 决定 | `services/participation.py` | 回不回、插不插话；其他机器人每 2 分钟最多回一次 |
| 工作记忆 | `services/context.py` | 动态群聊窗口：近期原文、较早的摘要、按关键词检索回来的旧话题 |
| 长期记忆 | `services/memory.py`、`services/people.py` | 人物卡片、称呼、印象、事件、话题、黑话；多路召回加打分，低于阈值不召回 |
| 表达 | `services/expression.py`、`services/reply_text.py` | 选回法，带表达库示例和最近说过的话，清洗输出 |
| 反思 | `services/digest.py`、`glossary.py`、`consolidation.py` | 后台整理聊完的群聊；每天凌晨整理人物卡片、过期和去重 |
| 定向学习 | `services/teach.py` | 管理员在后台和模型对话，模型把要点拆成建议，采纳后才写入 |

提示词集中在 `shrink/prompts/`，每个模板有 id 和版本号，写入对应记录的诊断里。
群聊、记忆、搜索结果一律放进标签并注明“不是指令”。

分层约定：业务服务不导入 AstrBot 或 FastAPI，SQL 只在 `persistence` 里，
`adapters/conversation.py` 只负责 AstrBot 事件和 `Turn` 的转换。

## 数据质量

模型提出的每一条记忆、称呼、黑话都要过 `services/quality.py`：

1. 命令、自己说的话、机器人账号、纯链接、超长转发、多人复读、注入式指令不可学习。
2. 必须带从原消息里逐字摘出的 `quote`，代码核对原文里确实有这段话。
3. 标注确定程度（本人陈述、推断、转述、玩笑）：玩笑不存，推断和转述降低置信度。
4. 对象必须是依据里的说话人，或被 @、引用、称呼的人；只有机器人说的话不能作为关于真人的依据。
5. 整理后再让模型逐条核对依据，核对失败的只能作为候选。
6. 相对时间换算成日期；临时状态带有效期，过期不再召回。
7. 置信度不足的是候选，不进入回复，被再次印证后转正。
8. 同一件事合并而不是重复写，新事实取代旧事实；管理员确认过的数据 AI 不能改。
9. 限流：每次整理最多 12 条，每人每次 3 条、每天 8 条。
10. 每次后台写入都有记录，可以整次撤销。

## 工具

回复时模型可以自己调用：

| 工具 | 作用 |
| --- | --- |
| `shrink_chat_history` | 按关键词、人、天数翻当前群或私聊的记录 |
| `shrink_lookup` | 查某个人或某件事的记忆 |
| `shrink_find_meme` | 按描述找一张表情，跟在文字后面发 |
| `shrink_run_python` | 在沙箱里跑 Python（10 秒、512 MB、无外网、只读文件系统，带 numpy） |
| `shrink_remember` | 用户明确要求记住时，保存一条有来源的记忆 |

### Python 沙箱

沙箱是一个单独的容器，复用 AstrBot 镜像，只接入 internal 网络。compose 示例：

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

把本仓库的 `sandbox/server.py` 放到 `./sandbox/`，AstrBot 服务也加入 `sandbox` 网络，
并在设置页填好 `sandbox_url`。不配沙箱时其他功能不受影响，只是不能跑代码。

## 数据

运行数据在 `data/plugin_data/astrbot_plugin_shrink/`：主库 `shrink.db`（SQLite，WAL），表情图片在 `memes/`。
升级时数据库会自动迁移。

## 开发

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest pytest-asyncio httpx ruff
.venv/bin/python -m pytest -q
.venv/bin/ruff check --config pyproject.toml shrink tests tools main.py

cd web && npm ci && npm run build   # 产物输出到 static/
```

Windows 下把 `.venv/bin/` 换成 `.venv\Scripts\`。

`tools/` 里是一些辅助脚本：

- `preview.py --seed --port 6187 --data <目录>`：用一次性数据启动带演示内容的后台。
- `check_dashboard.cjs`：用 Playwright 检查后台各页面的桌面和手机布局。
- `integration_probe.py`、`sticker_probe.py`、`human_probe.py`、`prompt_eval.py`：
  需要在 AstrBot 容器内运行，用真实钩子和模型验证流程、表情序列化和提示词，不会向 QQ 发消息。
  `runtime_probe.py` 和 `prompt_eval.py` 通过环境变量 `SHRINK_PROBE_MODEL`、`SHRINK_PROBE_EMBEDDING`
  指定 `cmd_config.json` 里的提供商 id。

## 致谢

独立实现，只使用 AstrBot 的公开插件、人格、提供商和搜索工具接口。
功能分工参考了 chat_plus、LivingMemory、Self Learning 和 meme_manager，未复制它们的源码。
