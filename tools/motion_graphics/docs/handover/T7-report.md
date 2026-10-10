# ◆ T7-r2 动态图形修订报告

日期：2026-10-04。首轮主管视觉评审为 `7/10`；本轮针对五项反馈修订，等待复审。

## ◆ 本轮改动清单

| 反馈 | 修订 | 审核证据 |
|---|---|---|
| `trend` 缺少早期线条生长 | 折线从 `0.45s` 起按数据点逐段向右延伸，增加移动线头，节点和数值在线头到达后显示；说明文字移至 `1.7s` | `trend_10.png` 的 `0.791667s` 帧已有第一段及第二段线头 |
| 两个数据版式下半部空白、头重脚轻 | `stat_highlight` 的比较改为两张横向卡片；比较卡片下移；两个版式均增加底部结论区和金色侧线，结论面板底部为 `818px` | 总览的 `50% / 90%` 帧 |
| 入场以淡入堆叠为主 | 标题纵向 mask、标签/流程描述横向 mask、卡片 `100ms` 错帧位移、边线和柱条伸展；大数字首个可见帧为 `0`，随后滚动至目标 | `entrance_sheet.png` 的四组编码关键帧；数字起点回归测试 |
| `comparison` 大数字空窗 | 结论数字与卡片同在 `1.3s` 入场；新增显式 `sync_with: "comparison"`，把两者合并为 `1s` 入场组 | `comparison_50.png` 的 `4s` 帧为完整 `93%`；首个可见帧同时出现卡片数字和结论 `0` |
| 折线终点数字出图表区 | 终点标签在终点左侧右对齐，标签顶部限制在图表内部 | `trend_90.png`；`94` 和宽数值 `9,999` 的边界回归测试 |

`stat_highlight.json` 的任务卡数字及时间表保持不变。`trend` demo 主数字的 `from` 改为 `0`，最终数据仍为 `94%`；普通 `number_roll` 继续尊重指定的 `from`。

## ◆ 文件与重建

- 代码：`renderer.py`、`spec_schema.py`、`build_demos.py`、`test_motion_graphics.py`。
- Spec：`demo/comparison.json`、`demo/trend.json`。
- 文档：[README](../../README.md)、本报告。
- 产物：四段 MP4、四张最终预览、十二张 `10% / 50% / 90%` 抽帧、审核总览、入场总览、两个验证 JSON 和运行日志。
- `build_demos.py --rebuild` 在工具目录内暂存四段成片，全部渲染和验证通过后替换固定 demo 产物；默认模式及普通渲染 CLI 继续拒绝覆盖。
- 同步组仅允许 `comparison` 版式的 `number_roll`，必须与卡片同一 `t`；其他入场区间不得与同步组重叠，无序时间表仍有效。

复现命令（仓库根目录）：

```sh
.venv-workflow/bin/python -B tools/motion_graphics/build_demos.py --rebuild
.venv-workflow/bin/python -B tools/motion_graphics/test_motion_graphics.py -v
```

## ◆ 验证结果

- `22` 项测试全部通过：原有 `16` 项，加六项修订回归，覆盖早期折线增长、同步组约束、首帧零值、数字/卡片同步、终点边界、下半部数字占用。
- 四段视频均为 `540×960`、`24fps`、H.264、`yuv420p`，无音轨。
- 每段最后 `12` 个解码帧逐像素一致，最大色值差为 `0`。
- 已检查重渲后的十二帧审核总览和十六帧入场总览：结论区与来源注释无碰撞，终点标签处于图表范围内。视觉评分留待主管复审。

| layout | 帧数 | 成片时长 | `10% / 50% / 90%` 实际抽帧时间 |
|---|---:|---:|---|
| `stat_highlight` | `199` | `8.291667s` | `0.833333 / 4.166667 / 7.458333s` |
| `comparison` | `192` | `8s` | `0.791667 / 4 / 7.208333s` |
| `process` | `192` | `8s` | `0.791667 / 4 / 7.208333s` |
| `trend` | `192` | `8s` | `0.791667 / 4 / 7.208333s` |

审核入口：[十二帧总览](../../demo/review_sheet.png)、[入场关键帧](../../demo/entrance_sheet.png)、[编码验证](../../demo/verification.json)、[入场抽帧时间](../../demo/entrance_frames.json)、[重建日志](../../demo/build.log)、[测试日志](../../demo/test.log)。

成片：[数字强调](../../demo/stat_highlight.mp4)、[左右比较](../../demo/comparison.mp4)、[流程](../../demo/process.mp4)、[趋势](../../demo/trend.mp4)。

## ◆ 范围与交接

- 所有本轮写入均在 `tools/motion_graphics/` 内；未接入生产，未改动生产脚本或状态，未安装依赖，未调用网络或付费 API。
- 请求同时指定更新根目录 `docs/handover/T7-report.md` 和禁止修改工具目录外文件；按最后的范围限制，将本轮报告保存为 `tools/motion_graphics/docs/handover/T7-report.md`，根目录首轮报告保留。
- 目录外原有 tracked diff 与开工快照一致；未 commit / push。媒体仍受仓库既有 ignore 规则影响，文件实际已落盘。
