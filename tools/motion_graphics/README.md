# Motion graphics

离线声明式动态图形：`540×960`、`24fps`、无音轨；独立工具，未接入生产。

## 运行

在仓库根目录使用现有环境：

```sh
.venv-workflow/bin/python -B tools/motion_graphics/renderer.py tools/motion_graphics/demo/stat_highlight.json tools/motion_graphics/new-shot.mp4 --preview tools/motion_graphics/new-shot.png
.venv-workflow/bin/python -B -m unittest discover -s tools/motion_graphics -p 'test_*.py' -v
.venv-workflow/bin/python -B tools/motion_graphics/build_demos.py --rebuild
```

`--preview` 保存最终静止构图。输出已存在时退出码为 `2`，不覆盖；输入错误同样返回 `2` 并指出字段。MP4 用固定 `QP=18` 的 H.264 全帧内编码、`yuv420p`、`faststart`，便于时间线寻址，并确保尾部解码帧一致；文件比长 GOP 编码大。

## Spec

必填根字段：`shot_id`、`duration_seconds`、`layout`、`title`、`reveals`。完整示例见 `demo/*.json`。

| layout | 核心 reveal | 版式 |
|---|---|---|
| `stat_highlight` | `number_roll` | 两条比较卡片，底部大数字结论区 |
| `comparison` | `comparison` | 左右数字卡片，底部结论数字可同步入场 |
| `process` | `step` | 最多三个编号流程步骤 |
| `trend` | `trend` | 零基线折线，3–6 个等间距分类点 |

各 reveal 必填 `t`、`kind`；标题直接来自根 `title`。

| kind | 额外必填字段 | 入场占用时间 |
|---|---|---|
| `title` | 无 | `0.45s` |
| `stat` | `text`；可选 `emphasis: primary/secondary` | `0.45s` |
| `comparison` | `left/right` 各含 `label/value/unit` | `0.95s` |
| `number_roll` | `text/from/to/suffix` | `1.00s` |
| `step` | `title/text` | `0.65s` |
| `trend` | `points: [{label,value},…]`、`unit` | `1.20s` |
| `source_note` | `text` | `0.45s` |

- 元素入场后保留。不同入场组的区间不得重叠；比较两列、流程主副文案和趋势类别标签的内部 stagger 为 `100ms`，已计入占用时间。时间表可无序。
- 仅 `comparison` 版式的 `number_roll` 可加 `sync_with: "comparison"`，且 `t` 必须与比较卡片一致。两者按最长的 `1s` 入场合并成组；不允许其他 reveal 与该组重叠。未声明同步时沿用独立时间表。
- 同种持久槽只允许一个 reveal，`step` 除外。省略 `title` reveal 时，自动预留从 `0s` 开始的标题入场区间。
- 时长限 `2–60s`。帧数取 `round(duration_seconds × 24)`；最后 `12` 帧冻结，所有入场必须在冻结边界前完成。
- 比较和趋势值非负，且至少有一个非零值；数字最多两位小数、格式化后不超过六字符。拒绝非有限值、布尔数值以及过精确数据，避免悄悄改动数字。
- 字符数量和实际排版均检查；过长文本报错。标题最多 `24` 字，流程标题 `14` 字、描述 `38` 字。数值位数多时自动缩字号，demo 主数字为 `114–152px`。
- `number_roll.text` 可写完整句子或标签；渲染器剥离匹配的最终数字与单位，把数字单独排版。
- 趋势点是等间距类别轴，不解释为真实时间间隔；柱长依据两值的共同最大值计算，不人为夸大较小值。

## 视觉与依赖

Pillow/numpy 双倍分辨率绘制并下采样，背景为深青黑渐变、静态细网格/颗粒与暗角；强调色仅 `#E8C468`，其余文字灰阶。安全边距 `48px`，中文首选系统 `STHeiti`，可回退思源黑体 / Noto Sans CJK；数字首选系统 DIN。

依赖仅现有 `Pillow`、`numpy`、系统字体、`ffmpeg/ffprobe`。不联网、不装依赖、不调用付费 API。

标题纵向 mask、标签/流程描述横向 mask、卡片错帧位移和边线伸展组成入场编排；卡片数字从零起，`number_roll` 首个可见帧保留 `from`（本轮 demo 均为 `0`）。趋势线按数据点从左向右逐段延伸，节点与数值标签在线头到达后显示；终点标签向左对齐并限制在图表内。`trend` demo 从 `0.45s` 启动折线，`10%` 帧已有第一段及第二段线头。

`build_demos.py` 调用公开 CLI 渲染四个 demo，按目标时长的 `10%/50%/90%` 抽取编码帧，生成 `demo/review_sheet.png`，验证编码属性和解码尾帧。`demo/entrance_sheet.png` 额外展示卡片错帧、结论数字和折线生长的入场关键帧。精确时间记录于 `verification.json` / `entrance_frames.json`。

默认拒绝覆盖；`--rebuild` 在工具目录内暂存全部成片，通过验证后只替换固定 demo 产物。普通渲染 CLI 仍拒绝覆盖。T7-r2 改动与验证见 [交接报告](docs/handover/T7-report.md)。
