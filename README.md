# MiniMax Video Workflow

批量短视频（抖音竖版）生产管线：素材入库 → 叙事规划 → 分镜/口播/配乐 →
镜头生成 → 合成 → 导出 → 交付门与三区看板。

## 结构

- `workflow/` — 管线本体：队列调度（SQLite 单写者 + flock 幂等）、LLM 通道
  （云端 worker / Anthropic-messages 双 transport）、叙事门（sha 绑定 +
  契约校验，可选 format-only 模式只做技术门）、交付策略、产品账本、
  看板服务（dashboard + board）
- `tests/` — 契约与行为测试（`pytest tests/`）
- `scripts/run_user_repair30.py` / `run_deadline30.py` — 常驻生产 driver
  （单例 flock、渲染/审计子进程管理、锁容错）

## 运行前提

- Python 3.12+，`pip install` 依赖（见 `pytest` 导入面：numpy、edge_tts、
  av、faster-whisper 等）
- ffmpeg 在 PATH
- LLM key 经环境变量注入（`api_key_env` 指定变量名，代码永不落盘）

## 安全模型

- API key 只从环境变量或用户 shell 配置读取（`workflow/relay_credentials.py`），
  仓库内无任何凭据本体
- 看板写操作需 `WORKFLOW_DASHBOARD_REVIEW_TOKEN`；默认仅监听 loopback，
  远端访问用 `--host` + 视图白名单显式开启
- 媒体服务有路径白名单（记录在案的交付物才可访问）
