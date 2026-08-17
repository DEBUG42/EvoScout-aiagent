# EvoScout AI Agent

EvoScout AI Agent 是一个通过飞书控制的本地 AI 研究助理中枢。它的重点不是简单推送论文，而是让你用一句话创建和调整子 AI 机器人：新的关注领域、新的论文/新闻订阅、新的摘要偏好，都会被 master 写入本地 Agent 配置并热加载。

## 核心特色

- **一句话创建子 AI**：例如“创建一个关注 AI Agent benchmark 的机器人”
- **一句话改关注领域**：例如“把 aipapers 改成关注多模态、视频理解和世界模型”
- **自更新 Agent 配置**：每个机器人都是 `agents/*.md`，master 可以读取、修改并热加载
- **飞书手机控制**：论文、新闻、状态、截图、记忆、脚本执行都可以从飞书发起
- **本地运行**：数据库、缓存、日志、记忆和凭证都保存在你的电脑上

默认包含三个机器人：

| 机器人 | 作用 |
| --- | --- |
| `master` | 主控 AI，负责理解你的自然语言、修改配置、创建子 AI、管理记忆和调用工具 |
| `aipapers` | AI 论文机器人，默认关注 LLM、Agent、RAG、多模态、推理、评测和对齐 |
| `ainews` | AI 新闻机器人，默认关注模型发布、开源模型、AI agent 和行业事件 |

## 安装

```powershell
git clone https://github.com/DEBUG42/EvoScout-aiagent.git
cd EvoScout-aiagent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config\.env.example config\.env
Copy-Item config\config.example.yaml config\config.yaml
```

## 配置 1：填写 API Key

打开 `config/.env`，填写 DeepSeek API Key：

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key
```

继续填写飞书应用凭证。一组 `master` 应用凭证就够了，其他机器人默认通过 master 中转推送和控制。

```dotenv
LARK_APP_ID_MASTER=your_master_lark_app_id
LARK_APP_SECRET_MASTER=your_master_lark_app_secret
```

对应关系：

| 飞书应用 | 用途 | `.env` 变量 |
| --- | --- | --- |
| master 应用 | 手机入口、自然语言控制、论文/新闻中转推送 | `LARK_APP_ID_MASTER` / `LARK_APP_SECRET_MASTER` |

## 配置 2：创建飞书应用

创建一个飞书企业自建应用即可。这个应用作为 master 入口，负责接收你的消息，并把 `aipapers`、`ainews` 和后续新增机器人的结果推送给你。

飞书后台操作：

1. 打开飞书开放平台，创建“企业自建应用”
2. 进入“凭证与基础信息”，复制 App ID 和 App Secret 到 `config/.env`
3. 进入“应用功能”，启用“机器人”
4. 进入“权限管理”，添加并开通：
   - `im:message:send_as_bot`
   - `im:message.p2p_msg:readonly`
   - `im:message.group_at_msg:readonly`
   - `im:resource`
5. 进入“事件与回调 -> 事件订阅”，订阅方式选择“使用长连接接收事件”，添加事件 `im.message.receive_v1`
6. 进入“事件与回调 -> 回调配置”，添加回调 `card.action.trigger`
7. 进入“版本管理与发布”，创建版本并发布；可用范围建议只选择自己或可信用户

注意：每次修改权限、事件、回调或可用范围后，都要重新发布应用版本。

## 配置 3：调整运行配置

打开 `config/config.yaml`，常用只需要改这些：

| 配置项 | 用途 |
| --- | --- |
| `ai.interests` | 默认关注领域 |
| `ai.max_daily_calls` | 每个机器人每天最多调用 LLM 的次数 |
| `push.digest_time` | 每日摘要推送时间 |
| `sources.*.enabled` | 是否启用 arXiv、Semantic Scholar、Hacker News、Reddit、RSS 等来源 |
| `security.allowed_users` | 允许使用机器人的飞书用户；为空时绑定第一个发消息的人 |
| `security.shell_allow_prefixes` | `/shell` 允许执行的命令前缀 |
| `security.scripts` | `/run` 可执行的脚本白名单 |

## 验证飞书

```powershell
.\.venv\Scripts\python.exe scripts\feishu_smoke.py master
```

运行后，在 60 秒内给 master 机器人发一条消息。成功时，你会在飞书收到文本、富文本和卡片三种测试消息。

## 启动

单轮抓取验证：

```powershell
.\.venv\Scripts\python.exe run.py --once
```

常驻运行：

```powershell
.\.venv\Scripts\python.exe run.py
```

崩溃自动重启：

```powershell
.\.venv\Scripts\python.exe run.py --supervise
```

## 飞书里怎么用

直接给 `master` 发自然语言：

```text
创建一个叫 agentbench 的子 AI，关注 AI Agent benchmark、tool use 和 workflow automation。
每天只推相关度 7 分以上的论文，摘要里说明任务、方法、数据集和结论。
```

```text
把 aipapers 改成重点关注多模态大模型、视频理解、世界模型和 embodied AI。
```

常用命令：

| 命令 | 功能 |
| --- | --- |
| `/help` | 查看全部命令 |
| `/status` | 查看电脑状态 |
| `/shot` | 截屏回传 |
| `/papers [n]` | 查看最近论文 |
| `/news [n]` | 查看最近新闻 |
| `/sub list\|add\|del` | 管理订阅 |
| `/translate <id>` | 翻译论文摘要 |
| `/memory show\|add\|set` | 管理机器人记忆 |
| `/run list\|<name>` | 执行白名单脚本 |
| `/shell <cmd>` | 执行白名单命令，危险命令需要确认 |

## 新增机器人

推荐直接让 master 创建：

```text
创建一个 aiinfra 机器人，关注模型推理优化、KV cache、GPU 调度和 vLLM。每天推送最相关的论文和工程新闻。
```

master 会生成新的 `agents/<name>.md`。新机器人的结果默认仍由 master 飞书应用推送，不需要再配置新的 App ID。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| `DEEPSEEK_API_KEY 未配置` | 检查 `config/.env` 是否存在，以及变量名是否正确 |
| 飞书收不到消息 | 检查 App ID/Secret、应用是否发布、可用范围是否包含你 |
| 机器人收不到用户消息 | 检查事件订阅是否为“长连接”，并添加了 `im.message.receive_v1` |
| 卡片按钮无响应 | 检查回调配置里是否添加并发布了 `card.action.trigger` |
