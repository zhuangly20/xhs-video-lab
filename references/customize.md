# 改造菜谱（customize）

用户说「改造」时，先列这个菜单让他选，然后按对应菜谱执行。改完任何东西都要跑一次 `workbench_build.py` 验证没改坏。

## 菜单

1. 给表格/工作台加新字段
2. 新增或更换博主
3. 改工作台外观（配色/布局/列）
4. 换转写模型提速
5. 迁移 Base / 换表
6. 调整钩子/领域分类

## 1. 加新字段（如"发布平台"、"我的评分"）

三处同步，顺序固定：
1. **analysis-rubric.md** 的 schema 加字段定义（含示例值）
2. **Base**：`lark-cli base +field-create --base-token <t> --table-id <t> --json '{"type":"...","name":"..."}'`；同时改 `bitable_write.py` 的 FIELDS 列表和行构造
3. **工作台**：`assets/工作台模板.html` 明细表 cols 数组加列 + 行模板加单元格；总览页按需加统计卡
4. 历史数据补值：逐条更新 analyses/*.json 后重跑 workbench_build.py；Base 旧记录用 `+record-batch-update`

## 2. 新增博主

什么都不用改——博主是 select 字段，`bitable_write.py` 自动补选项。直接丢新博主视频的链接即可。若要给博主配色/分组，改 taxonomy.md 加说明。

## 3. 改工作台外观

- 配色：模板顶部 `:root` CSS 变量（`--red` 主色、`--bg` 底色等），改一处全局生效
- 明细表列：搜 `const cols = [` 调整列定义
- 总览卡片：`renderOverview()` 内的 stat-grid
- 拆解页模块：`renderDetail()` 内各 card 块，增删即可
- **不要动** `renderList/renderDetail` 对 `window.CASE_DATA` 字段名的引用，除非按菜谱 1 同步 schema

## 4. 换模型提速

config.json 改 `"model": "large-v3-turbo"`（快约 8 倍，精度略降，中文场景够用）。首次使用会自动下载新模型（约 1.6GB，走代理）。改回 `large-v3` 即可还原。

## 5. 迁移 Base / 换表

1. 新 Base：删掉 config.json 的 `base_token` 和 `table_id` 两行，下次跑 `bitable_write.py` 会自动重建并回存
2. 只换表：改 `table_id`（表结构需一致，用 `+field-list` 核对）
3. 旧表数据迁移：`+record-list` 导出 → 新表 `+record-batch-create`（注意 pitfall 4.4：record-list 是 markdown 输出）

## 6. 调整分类

按 taxonomy.md 文末的三处同步规则执行。已入库的分析 JSON 批量改值后：重跑 `workbench_build.py`，Base 侧用 `+record-batch-update` 按记录 id 更新对应字段。
