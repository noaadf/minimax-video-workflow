# Text compositor

离线文字平面合成器。仅依赖现有 `Pillow`、`numpy` 和本地字体，不调用网络或生产管线。

## CLI

在仓库根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-workflow/bin/python tools/text_compositor/composite.py \
  --base tools/text_compositor/demo/base_signboard.png \
  --text '义乌国际商贸城' \
  --quad 111,284 618,309 612,410 111,382 \
  --font-size 62 --color '#E8C468' --mode signboard \
  --out tools/text_compositor/demo/cli_signboard.png
```

- `--quad`：左上、右上、右下、左下，浮点像素坐标；必须为凸四边形。可部分超出底图，但必须有可采样区域。
- `--font-size`：最大字号，工具同时按可用宽度和高度缩小；不放大、不通过缩放位图压扁字形。
- `--text`：保留原文，不自动折行；多行使用真实换行，例如 shell 的 `$'设备铭牌\n额定电压 220V'`。行基线距离为字号的 `1.15` 倍。
- `--font`：可选本地字体文件。默认中文使用 `STHeiti`，也搜索思源黑体及其 `Noto Sans CJK` 版本；英文屏幕使用系统等宽字体。
- `--color`：Pillow 支持的颜色，如 `#E8C468`、`#99EFCB`。
- `--shadow`：投影不透明度，范围 `0–1`，默认 `0.28`。`--shadow 0` 关闭投影，保留轻微边缘 AO；`--shadow 0.5` 加强投影。拒绝负数、超范围及非有限值。
- `--out`：PNG 文件；父目录须已存在，禁止覆盖输入底图。成功时打印实际字号、字体路径及平面尺寸的 JSON。

| 模式 | 版式 |
|---|---|
| `signboard` | 原生紧凑字距，逐行水平居中，整体垂直居中 |
| `label` | 最大字号的 `72%` 起步，细边框 |
| `screen` | 最大字号的 `86%` 起步，统一字符格宽，微发光，弱化光照衰减 |
| `sticker` | 最大字号的 `92%` 起步，浅色圆角底板；适合深色文字，如 `#263B32` |

## API

```python
from tools.text_compositor.composite import composite_text

result = composite_text(
    base, "义乌国际商贸城",
    ((111, 284), (618, 309), (612, 410), (111, 382)),
    font_size=62, color="#E8C468", mode="signboard", shadow=0.28,
)
```

`base` 为 Pillow 图像；返回同尺寸的新 RGBA 图像，不修改输入。底图颜色按 RGB 采样；不做 ICC 色彩管理。要求同一字体文件及相同依赖版本以保持跨运行字节一致。

投影只取文字字形 alpha，在源平面的局部右/下方向偏移 `(3, 4)px`（距离 `5px`），使用 `1.8px` 高斯模糊，再随 quad 透视映射；最终图中的距离随透视缩放。AO 使用 `0.85px` 模糊形成窄接触暗边，最大强度 `10%`，同时轻微压暗字形内缘。四种模式默认启用；铭牌边框、贴纸底板和屏幕发光不作为投影轮廓，贴纸文字投影落在底板上。

## 验证与示例

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-workflow/bin/python -m unittest tools.text_compositor.test_text_compositor -v
PYTHONDONTWRITEBYTECODE=1 .venv-workflow/bin/python tools/text_compositor/generate_demo.py
```

`demo/base_signboard.png` 是程序自绘的 `720×1280` 无字底图。`demo/{signboard,label,screen}.png` 各只合成对应平面，`*_comparison.png` 为原图与合成图并排对比。`contact_sheet.png` 汇集三组对比；`manifest.json` 记录文字、四边形、字体、实际字号、光照采样及合成图 SHA-256。

本轮 `*_r1.png` 复现无投影/AO 的原合成；`*_r2_comparison.png` 对比相同文字的 r1/r2 全图，`*_r2_detail.png` 为目标区域 `3×` 放大对比，`r2_contact_sheet.png` 汇集三组局部对比。`manifest.json` 同时记录效果参数和全部 PNG 哈希。此 demo 使用既有程序底图，主管真实底图未包含在本工具目录中。

`transform.py` 可单独使用：`homography` / `project_points` 为正向几何变换，`find_transform(输出角点, 输入角点)` 给出 Pillow 逆采样系数。先渲染原生字形，再一次性透视变换；预乘 alpha 避免黑边。约 `1–2px` 羽化限制在目标四边形内，外部像素保持原值。
