# 技术文档

本文档包含 SuiPo 项目的技术细节、安装配置和开发说明。

## 功能模块

- `bili.py` - 拉取某个 UP 主的视频列表与字幕，写入 `data/videos.json`
- `summarize_videos.py` - 生成视频摘要与合并后的字幕文本，输出到 `data/videos_summary.json`
- `analyze_reviews.py` - 调用 Azure OpenAI 识别探店视频并抽取字段，输出到 `data/video_reviews.json`
- `generate_readme_table.py` - 将分析结果生成 README 中的表格
- `generate_map.py` - 生成中国地图标注探店城市和次数

每一步都有索引文件，避免重复处理未变化的数据。

## 依赖

- Python 3.9+
- `requests`
- `openai`（Azure OpenAI 客户端）
- `matplotlib`（地图生成）

## 安装

```bash
pip install requests openai matplotlib
```

## 环境变量

### bili.py（可选，字幕需要登录时使用）

| 变量 | 说明 |
| --- | --- |
| `SESSDATA` | Bilibili Cookie 中的 SESSDATA |
| `COOKIE` | 浏览器复制的完整 Cookie 字符串（可覆盖/补充 `SESSDATA`） |

### analyze_reviews.py

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI 端点（必填） | - |
| `AZURE_OPENAI_KEY` | Azure OpenAI 密钥（必填） | - |
| `AZURE_OPENAI_API_VERSION` | API 版本 | `2024-12-01-preview` |
| `AZURE_OPENAI_DEPLOYMENT` | 部署名称 | `gpt-5.2-chat` |
| `AZURE_OPENAI_SLEEP_S` | 请求间隔（秒） | `0.5` |

## 使用方法

### 1. 配置目标 UP 主

在 `bili.py` 中修改目标 UP 主 ID：

```python
VMID = 3546888255048212  # 替换成目标 up 主 mid
```

### 2. 拉取视频和字幕

```bash
python bili.py
```

### 3. 生成摘要与合并字幕

```bash
python summarize_videos.py
```

### 4. 识别探店/餐馆测评信息

```bash
python analyze_reviews.py
```

### 5. 更新 README 表格

```bash
python generate_readme_table.py
```

默认仅包含 `is_food_review=true` 的条目；如需包含全部视频，添加 `--all`。

### 6. 生成探店地图

首次运行需下载中国地图数据：

```bash
python generate_map.py --download
```

然后生成地图：

```bash
python generate_map.py
```

生成 `assets/china_map.png` 和 `assets/china_map.svg`，标注各城市探店次数。

## 产出数据

| 文件 | 说明 |
| --- | --- |
| `data/videos.json` | 原始视频信息与字幕记录 |
| `data/subs/*.json` | 字幕文件 |
| `data/videos_summary.json` | 精简统计与合并字幕文本 |
| `data/video_reviews.json` | 探店识别与字段抽取结果 |
| `assets/china.geojson` | 中国地图边界数据 |
| `assets/china_map.png` | 探店地图（PNG 格式） |
| `assets/china_map.svg` | 探店地图（SVG 格式） |

## 备注

- Bilibili 接口可能限流，`bili.py` 会在请求间休眠
- 如果网页接口被限制，`bili.py` 会回退到 app cursor API
