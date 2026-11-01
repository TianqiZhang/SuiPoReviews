# 技术文档

## 流程与范围

保留原有小脚本流程：`bili.py` → `summarize_videos.py` → `analyze_reviews.py` → README / 地图。
采集改用 yt-dlp，不再自行维护 B 站签名和备用接口。仅获取元数据和中文字幕，不下载视频/音频，也不自动转写。

这次改造不重建数据库、不引入任务服务、不改变原有单条餐厅结果结构。旧的 `data/videos.json`、字幕 JSON 和餐厅结果继续兼容。

## 安装

Python 3.11+（锁定的 yt-dlp 最低要求），本次测试环境为 Python 3.12。进入仓库根目录：

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell 改用：.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

依赖文件锁定本次验证的 yt-dlp / OpenAI 客户端版本。B 站后续变更时，先在虚拟环境升级 yt-dlp、做单视频测试，再更新版本记录。

## 先小样本，再全量

建议先备份 `data/`，尤其不要删除已有 75 份字幕。

```bash
# 单视频测试；默认不使用登录信息
python bili.py --bvid BV1qFsZ6REP2
# 或仅查看并处理 UP 主最新 3 个视频
python bili.py --limit 3
```

`--limit` 在采集阶段限制选中的视频数，不是保证下载成功的数量。默认 UP 主仍为特厨隋坡（3546888255048212）。

### 字幕需要登录时

在你自己的电脑使用本人授权的 B 站登录会话，准备 **Netscape 格式**的 Cookie 文件（不是直接复制请求头的文本），并放在仓库外。再运行：

```bash
python bili.py --bvid BV1qFsZ6REP2 --cookie-file /absolute/path/to/bilibili.cookies.txt
```

不要把 Cookie、SESSDATA、API key 发到聊天、提交到 Git 或写入日志。旧脚本的 `COOKIE` / `SESSDATA` 环境变量不再使用。Cookie 过期需要你在本地更新；脚本不会自动登录。遇到登录要求或访问限制应先处理原因，不轮换代理或身份绕过限制。

确认单视频成功后：

```bash
# 发现全部公开视频，只补缺失/无效的字幕；复用已经有效的旧字幕
python bili.py --cookie-file /absolute/path/to/bilibili.cookies.txt
python summarize_videos.py
```

也可用 `--bvid` 多次指定视频，或 `--url-file videos.txt` 提供每行一个 BV 号/视频链接。采集、字幕整理、模型分析都支持 `--data-dir`，可在复制的数据目录做隔离测试；旧的 `data\subs\...` 路径会兼容读取，新路径统一为数据目录内的 POSIX 相对路径。

### 配置模型，先分析两条

支持 OpenAI 官方 API 和原有 Azure OpenAI。使用官方 API 时在自己的终端设置：

```bash
export LLM_PROVIDER=openai
# macOS / zsh：隐藏输入，避免 key 留在命令历史中
read -rs 'OPENAI_API_KEY?粘贴 OpenAI API key 后回车: '; echo
export OPENAI_API_KEY
# 可选；默认已为这个模型
export OPENAI_MODEL=gpt-6-luna
python analyze_reviews.py --limit 2
```

默认选择 `gpt-6-luna`，用于字幕分类和餐厅信息抽取，使用 JSON 输出、`reasoning_effort=none` 和 1600 token 输出上限以控制成本（为避免截断保留充足余量，按实际用量计费）。按 2026-10-31 [官方价格](https://developers.openai.com/api/docs/models/gpt-6-luna)，标准输入 $0.10 / 百万 token、输出 $0.50 / 百万 token。例如每条 5,000 输入 + 300 输出 token，100 条约 $0.065（估算，不含重试；实际由用量决定）。已完成全量分析及剩余 10 条摘述复测；目前没有待处理摘述，仍有 4 条缺字幕记录。生成成功不代表已经逐条核实内容准确。`OPENAI_MODEL` 可覆盖模型名；其他模型不自动附加 Luna 的推理参数。

官方入口固定为 `https://api.openai.com/v1`，不读取 `OPENAI_BASE_URL`。只设置 `OPENAI_API_KEY` 也会自动选择官方 API；若两套凭据同时存在，默认优先 OpenAI，可用 `LLM_PROVIDER` 明确指定。未设置官方 key 而已有 Azure 配置时继续使用 Azure。更换 provider / 模型会使缓存失效并重新分析，轮换 API key 不会。

也可以复制 `.env.example` 为 `.env` 后在本地编辑。脚本不会自动加载 `.env`，运行前在仓库根目录执行 `set -a; source .env; set +a`。不要把实际 key 发到聊天或提交 Git。`LLM_SLEEP_S` 控制调用间隔，兼容旧 `AZURE_OPENAI_SLEEP_S`。

继续使用 Azure 时设置：

```bash
export LLM_PROVIDER=azure
export AZURE_OPENAI_ENDPOINT='https://YOUR-RESOURCE.openai.azure.com/'
export AZURE_OPENAI_KEY='YOUR-KEY'
export AZURE_OPENAI_DEPLOYMENT='YOUR-DEPLOYMENT'
# 可选：按实际部署支持的 API 版本修改
export AZURE_OPENAI_API_VERSION='2024-12-01-preview'
python analyze_reviews.py --limit 2
```

Windows PowerShell 使用 `$env:AZURE_OPENAI_ENDPOINT = '...'` 等对应写法。

确认结果后运行完整分析及展示生成：

```bash
python analyze_reviews.py
python generate_readme_table.py
python generate_map.py
```

模型部署必须是你实际可用的部署；代码中的默认名不是可用性承诺。真实模型调用可能产生费用。本次更改了提示词/校验规则/缓存版本，第一次分析会重跑旧的非空字幕，不只是新增视频。之后未变化的成功结果会跳过；用 `--limit` 控制每次分析数量，或用 `--bvid` 指定对象。`--force` 可强制重新整理/分析选中的条目。

## 状态与数据保护

- `data/review_overrides.json` 保存按 BV 号索引的人工确认记录，支持 `restaurant_name` 和 `is_food_review`，`note` 说明证据来源。人工确认优先于模型和标题；标题对同音字幕通常优先，但标题本身有错时采用人工确认。视频只给出的店铺简称可以作为店名，模型不应补出视频未提供的全名；人工提供的全名可在这里单独保存。
- 人工确认排除的视频无需模型或字幕即可写为非探店。更正店名在正常分析、摘述待处理、API 失败保留旧结果时均生效。修改有效校正字段会使对应记录缓存失效，修改备注不会；使用自定义 `--data-dir` 时，应将校正文件一起复制到该数据目录。
- 可在不调用模型的情况下应用人工记录：`python -c 'from analyze_reviews import DATA_DIR, apply_saved_manual_overrides; print(apply_saved_manual_overrides(DATA_DIR))'`。只更新已有结果，保留其他记录；被排除条目的分析错误记录会清除，其他错误仍保留待处理状态。
- “未获取字幕”“需要登录”“请求失败”不等于“不是探店”。这些记录保留为待处理，不进入成功缓存。
- 每个分 P 选择一份中文字幕，人工字幕优先，其次 AI 字幕；弹幕不作为口述文本。
- 已有字幕缺失或损坏时，整理阶段报错并保留上一份摘要，不会静默置空。
- 同一分 P 已有有效中文字幕时，采集阶段清理该分 P 的无效旧引用，保留有效的其他语言字幕和磁盘文件，避免补抓成功后仍被旧坏引用阻塞。
- 新视频整理失败、尚无摘要时，分析阶段也会记录上游失败并返回非零退出码；`--bvid` 和 `--limit` 同样适用于这些待处理条目。
- API 异常、无效 JSON 或餐厅核心字段类型错误不会覆盖先前结果。摘述缺失、类型错误或为空字符串时，更新已通过字段校验的店名、城市、评级，清空本轮无效摘述，保存 `summary_status=pending` 和安全的 `summary_error`；不会沿用可能属于旧店名的摘述。
- 提示词将字幕视为有同音错字、错误断句的 ASR 文本，结合正式开场的城市 / 店名 / 消费金额结构识别店名；同一家店的标题 / 简介明确写法优先于字幕同音错字，但不把菜品、类别或主持人名字当成店名。保留真实店名中的数字。
- 评价展示改为“评价摘述”：`review_summary` 允许补标点、删口头重复及无意义识别碎片、依据上下文修正明显同音错字，并压缩为简短评价；提示词要求保留优缺点、转折、否定和限制条件，不猜测会影响评价方向的模糊内容。摘述以全文字幕为依据，允许综合不同段落、换词表达，不要求对应连续原文。模型不再需要返回 `source_quote`；兼容旧响应时，仅将可匹配的原文保存为 `source_quote` / `quote`，无法匹配或缺失的辅助引文不会阻止摘述保存或进入成功缓存。代码只校验摘述结构（非空字符串或明确的 null），不做语义匹配，也不增加第二次模型审核；`complete` 表示生成及格式校验成功，不代表已经核实内容准确，质量通过人工抽查全文和摘述判断。
- `summary_status` 为 `complete`、`unavailable`（模型明确表示缺乏可用评价）或 `pending`。`pending` 不进入成功缓存，在错误文件记录 `summary_pending`，进程返回非零，统计中的 `metadata_only` 表示只更新了核心字段；重跑会重试。README 显示“摘述待处理”。旧记录没有摘述字段时继续显示旧原文，新记录优先显示摘述。
- 模型内部先区分 `single`（单店）、`multiple`（多家独立探店、无明确主店）和 `other`（年度回顾、做饭等非现场探店）。`other` 保存为非探店；`multiple` 保留探店标记，但代码清空单店名、评级和引文，避免把第一家评价当作整条视频唯一餐厅结果。省份不能直接填入城市字段。
- 本地校验失败会在 `video_reviews_errors.json` 的 `validation_error` 字段记录固定的失败说明；不保存模型原始响应或服务商异常正文。
- 使用内容哈希及提示词/模型/schema 版本判断缓存，而非文件修改时间。
- 索引和结果逐条原子写入；中断后可重跑。不要同时启动多个写入同一数据目录的进程。
- `videos_summary_errors.json` / `video_reviews_errors.json` 记录待重试条目。排除原因后重跑相应阶段。
- 非零退出码表示仍有失败或未完成项。看最终统计，不要因为生成了部分文件就认为全量更新成功。
- 餐厅、城市、评价或引文证据不足时允许 null。多店视频本轮仍保持原 schema，没有明确主店则不强行指定。

## 本次验证边界（2026-10-29）

- 浏览器能打开特厨隋坡主页，页面显示 144 个视频；最新视频 BV1qFsZ6REP2 可播放，字幕菜单显示登录后可用。
- yt-dlp 2026.10.26 的最新 3 条视频发现成功；同一最新视频的元数据/字幕列表查询成功，但匿名会话仅返回弹幕轨并提示字幕需登录。
- 这不是完整 144 条抓取成功的证明。带登录态字幕下载、真实 Azure 模型调用及最新餐厅数据刷新需由用户在本地继续验证。
- 34 项离线单元测试通过；旧数据回归保留全部 78 条原有字幕文本，75 条非空，3 条缺字幕明确报待处理；第二次运行更新 0 条。README 生成 66 行，地图生成成功，但云端缺少原脚本指定的中文字体，中文标签视觉效果未保证。仓库原始业务数据和展示文件不因测试而替换。

## 主要文件

- `bili.py`：yt-dlp 采集适配、增量字幕、状态记录。
- `summarize_videos.py`：兼容旧路径、字幕验证、文本及时间段汇总。
- `analyze_reviews.py`：OpenAI / Azure 模型抽取、结构校验、兼容旧引文、失败重试。
- `generate_readme_table.py` / `generate_map.py`：原有展示脚本，请在仓库根目录运行。
- `tests/`：离线测试，无需 Cookie 或模型 key。

参考：[yt-dlp](https://github.com/yt-dlp/yt-dlp) · [Bilibili extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
