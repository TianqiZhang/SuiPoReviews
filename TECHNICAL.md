# 技术文档

## 流程与范围

保留原有小脚本流程：`bili.py` → `summarize_videos.py` → `analyze_reviews.py` → README / 地图。
采集改用 yt-dlp，不再自行维护 B 站签名和备用接口。仅获取元数据和中文字幕，不下载视频/音频，也不自动转写。

这次改造不重建数据库、不引入任务服务、不改变原有单条餐厅结果结构。旧的 `data/videos.json`、字幕 JSON 和餐厅结果继续兼容。

## 安装

Python 3.10+，本次测试环境为 Python 3.12。进入仓库根目录：

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

保持原来的 Azure OpenAI 入口。以下值需要你在自己的终端设置，不要提交实际值：

```bash
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

- “未获取字幕”“需要登录”“请求失败”不等于“不是探店”。这些记录保留为待处理，不进入成功缓存。
- 每个分 P 选择一份中文字幕，人工字幕优先，其次 AI 字幕；弹幕不作为口述文本。
- 已有字幕缺失或损坏时，整理阶段报错并保留上一份摘要，不会静默置空。
- 模型异常、无效 JSON、错误字段类型或不匹配字幕的引文不会覆盖先前成功结果。
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
- `analyze_reviews.py`：Azure 模型抽取、结构与引文校验、失败重试。
- `generate_readme_table.py` / `generate_map.py`：原有展示脚本，请在仓库根目录运行。
- `tests/`：离线测试，无需 Cookie 或模型 key。

参考：[yt-dlp](https://github.com/yt-dlp/yt-dlp) · [Bilibili extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
