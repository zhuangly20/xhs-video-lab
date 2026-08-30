# 防呆清单（pitfall-list）

本项目从零跑通全流程踩过的所有坑。**现象 → 原因 → 正确做法 → 绝不要做**。动手前通读一遍，出故障先来这里对号入座。

## 1. 小红书访问与链接

### 1.1 链接必须带 xsec_token（最高频坑）
- **现象**：裸链接（`https://www.xiaohongshu.com/explore/<id>`）抓页面只有约 95KB 空壳（只有"想了解些什么？/做旅行攻略"等壳文案）；yt-dlp 报 `No video formats found`
- **原因**：小红书要求笔记链接携带 `xsec_token` 查询参数才返回笔记数据（视频流、点赞收藏数都在这份数据里）
- **正确做法**：让用户从浏览器地址栏**全选复制**完整链接（含 `?xsec_token=...&xsec_source=...`），或点"分享→复制链接"。给出具体复制指引话术
- **绝不要**：反复重试裸链接、尝试伪造 token、把这个当成网络问题排查
- 附注：token 有效期很长（实测隔 11 天仍可用），入库时把完整链接存进 library.json 可长期复用
- **例外（已内置解决）**：`xhslink.cn/o/xxx` 短链虽然本身不带 token，但跟随 302 跳转后的最终地址**自带 token**（App 分享链）。pipeline.py 的 `resolve_url()` 会自动解析，短链可直接处理；注意解析出的 token 是 URL 编码的（`%3D`=`=`），保持原样不要解码

### 1.2 小红书永远直连
- **现象**：挂代理抓小红书 curl exit 7（连代理失败，因为代理时开时关）
- **正确做法**：抓小红书（页面、音频）一律直连。脚本已强制绕过环境代理（ProxyHandler({})）
- **代理只用于**：HuggingFace 模型下载、GitHub 访问。默认端口 `http://127.0.0.1:7897`（备选 7890），用前先探测是否存活

### 1.3 cookies 路线是死路
- **现象**：`--cookies-from-browser edge` 在关闭全部 Edge 进程后仍报 `Failed to decrypt with DPAPI`（yt-dlp issue 10927）；Chrome 无配置目录
- **结论**：本机 cookies 路线基本走不通，且带 token 的链接根本不需要 cookies
- **绝不要**：再推荐用户导出 cookies 或关浏览器重试这条路线

### 1.4 yt-dlp 不支持博主主页枚举
- `https://www.xiaohongshu.com/user/profile/<id>` 报 `Unsupported URL`；搜索页/主页是动态渲染，静态请求拿不到作品列表
- **正确做法**：只处理单条笔记链接；用户想要"博主全部视频"时说明"需要逐条发链接，或把链接丢进飞书群"

## 2. 数据抓取

### 2.1 标题和时长必须用 yt-dlp 取
- 页面 HTML 里 grep `"title"` 只能得到壳文案（"想了解些什么？"），**取不到真实标题**
- **正确做法**：标题/时长用 yt-dlp 的 info dict（pipeline.py 已实现）；发布时间从页面 `"time":17xxxxxxxxxx` 毫秒时间戳取

### 2.2 互动数据位置与换算
- 位置：`window.__INITIAL_STATE__` → `note.noteDetailMap[<id>].note.interactInfo`，字段 `likedCount/commentCount/collectedCount/shareCount`
- 博主昵称：页面 `"nickname":"..."`
- **计数是中文格式**：`"1.3万"→13000`、`"2万"→20000`，脚本已自动换算；`videoDuration` 单位是毫秒

### 2.3 飞书群消息解析
- `+chat-messages-list` 返回的字段是 `content`（不是 `body.content`）；链接在 markdown 形式 `[标题](url)` 里，用正则提取 `https://www\.xiaohongshu\.com/[^\)\s]+`

## 3. 转写

### 3.1 模型下载
- hf-mirror.com 的 LFS 重定向与新版 huggingface_hub 不兼容（`FileMetadataError`）→ **必须走代理直连 huggingface.co**，设 `http_proxy/https_proxy` 环境变量
- large-v3 约 3.09GB，代理约 1.5-2MB/s，首次下载 30-40 分钟；下完后缓存在 workdir/models/ 离线复用
- Windows 无符号链接权限会有 cache 警告，无害可忽略
- **换 agent 后提示重新下载模型（已修复的坑）**：模型存在 workdir/models/（自包含设计），但其他 agent（豆包/通义/WorkBuddy 等的 agent 形态）自己写转写代码时会查**系统默认 HF 缓存**（`~/.cache/huggingface/hub`）——那里是空的就会重下 3GB。pipeline.py 的 `resolve_model_path()` 已支持双位置探测（素材库优先，默认缓存兜底，config 的 model_dir 最优先）；首次部署时可把 models 里的 `models--Systran--faster-whisper-*` 文件夹复制一份到默认缓存位置，让所有工具共享

### 3.2 转写速度与预期管理
- 本机无 NVIDIA 显卡，CPU/int8 下约 **2 倍实时**（311 秒音频约 10 分钟，536 秒约 18 分钟）
- **必须提前告知用户耗时**，批量处理时更要说清总时长
- 提速选项：`--model large-v3-turbo`（约快 8 倍，精度略降），在 config.json 改 `model` 字段

### 3.3 后台运行的日志缓冲
- Python 输出重定向到文件时不实时刷新，看起来像卡死
- **正确做法**：长任务加 `-u` 参数或 print(flush=True)（脚本已内置）；判断进程死活看 CPU 占用而非日志

## 4. 飞书多维表格（Base）

### 4.1 select 字段不自动加选项
- **现象**：写入报 `800030005 not_found`，提示选项不存在
- **原因**：Bitable 的 select 字段写入未知选项**不会**自动新增（与 sheets 不同）
- **正确做法**：先 `+field-list` 读现有选项，缺的用 `+field-update`（PUT 全量 options，旧+新一起交）补上再写。bitable_write.py 已自动化此逻辑
- **绝不要**：把枚举值改成近似词凑合，那会污染分类体系

### 4.2 field-update 的 flag 名
- 用 `--field-id`（不是 `--field-name`，没有这个 flag），但它的**值可以直接传字段名**

### 4.3 CellValue 格式
- datetime 用 `YYYY-MM-DD HH:mm:ss` 字符串；数字用 JSON number（不要千分位字符串）；单选用选项名字符串
- 批量写入单批 ≤200 行；并发写冲突（1254291）→ 串行 + 短暂等待
- `--json @文件` 的路径**必须是相对路径**（传绝对路径报 validation 错误）；大 payload（含逐字稿）写入时，把 payload 写进独立临时目录并以该目录为 cwd 执行 lark-cli（bitable_write.py 已内置此方案）

### 4.4 record-list 输出是 markdown 表
- `+record-list` 默认输出 markdown 表格（终端里中文还可能显示乱码），**不要**当 JSON 解析
- 验证记录数用 `+table-list` 的 `records_count`（这个是标准 JSON）

## 5. 工具安装（历史坑，env_check.py 已处理）

- winget 装 yt-dlp 会连带装 Deno/FFmpeg 依赖且从 GitHub 下载，国内网络必卡 → **yt-dlp 用 pip 装（清华镜像）**
- ffmpeg 用 `winget install Gyan.FFmpeg`（走 gyan.dev 不走 GitHub）；若安装中途被取消，PATH 链接可能缺失 → `--force` 重装补链接；找不到时回退路径 `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin`
- 模型下载慢时先测代理是否存活：`curl -x http://127.0.0.1:7897 https://github.com`（超时/000 = 代理没开，提醒用户开代理）

## 6. 工作台 HTML

- 页面缓存：改了 html/js 后浏览器可能用旧缓存，验证时加 `?v=N` 参数强制刷新
- 自动化浏览器里"键盘删除清空输入框"不触发 input 事件——这是自动化工具怪癖，**真实浏览器没问题**，不要为此改页面代码
- 工作台数据 schema 见 analysis-rubric.md，改字段必须模板与生成器同步

## 7. 效率优化清单（已内置于脚本，了解即可）

1. **幂等跳过**：transcripts/<id>.json 存在 → 跳过转写（省 10-20 分钟/条）；library.json 已有 → 整条跳过
2. **并行**：多条链接时下载与抓数据可并行（转写受 CPU 限制只能串行）
3. **复用**：完整链接存 library.json，以后重抓数据不用再要 token
4. **批量收尾**：攒够 3 条新案例再更新一次 insights.json，不要每条都重写
5. **一次网络动作取全部数据**：标题/时长/音频一次 yt-dlp 调用搞定，不要分开调
