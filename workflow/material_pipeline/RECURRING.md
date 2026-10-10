# 周期素材采集

每两小时回到当前素材对话执行。工作目录 `/path/to/workflow`。
配置 `research/material_pipeline/monitor_config.json`，来源身份见
`publication_sources.json`。网页是公众号的公开替代入口，不代表微信历史推送已采集。

## 每轮动作

2026-10-03污染修复：`monitor/pipeline_audits/source_story_gate_v2_20261003/inventory.json`
记录全部15份旧包；6份现有选题撤回，2份历史归档，2份日期/案例证据待补，5份来源故事待重审。
旧事实审核不再具有生产准入效力；旧制作请求已原样隔离，不能恢复文件名绕过新审核。
新选题在编导前须通过 `story_gate` v2：只给审稿模型完整原始来源和事实，不给素材作者的
标题、旁白或虚构情节。问题、实际行动、结果/完成阶段各有逐字原文证据，明确人物/团队、
主题价值、结果性质，拒绝仅功能/版本更新或靠虚构案例补故事。旁白最终复审还须通过
`source_story_fidelity`，制作请求同时绑定包、原文hash与v2选题审核回执；接收、准备和接纳前复核。
每轮的refresh最多2份推进也包括`held_story_review`旧稿的新来源专项审核：仅新增这个此前
不存在的审核阶段，有独立attempt和回执，不重跑原有事实/编导/画面/配音/视频任务。
专项通过只表示源故事可以重写；旧叙事或真实画面/配音失败仍转`material_needs_revision`，
保留具体缺口并修订后再走v2最终审核，禁止把旧请求或旧成片直接重新自动开工。

1. 运行 `.venv-research/bin/python -m workflow.material_pipeline.recurring_collect`。
   阅读返回的 `collection.json`、`monitor/state.json` 和前轮 `decisions.json`。
   网页快照保存在本轮 `sources/`，每轮抓取12个入口及最多8个文章页面。
   同时读取 `monitor/state.json.pending_evidence`：已有候选仍有日期或证据缺口时，
   按条目重试补证并另存sidecar；不能因为候选已入inbox就永久跳过补证。
   动态加载正文缺失时记录 `dynamic_body_missing`，不当作全文已读。
   已入素材包和已明确排除的URL跳过；未审阅URL不会因抓取成功而被永久消费。
   连续证据缺失的pending可记录`retry_after`；采集器暂缓到期前重抓，另列
   `deferred_pending`，到期自动恢复发现与重试，不转为已消费或已排除。
2. 对动态网页、正文缺失和入口故障，用公开搜索或可用浏览器查发布方页面，
   跟到原始公告、论文或代码仓库；原始网页仍按 `workflow.ingest.fetch` 归档，
   并将解析正文写入同目录 `source.txt`。登录、验证码或授权缺失如实记录，
   不把搜索摘要或标题当成全文。优先四个用户推荐来源，其余来源轮流覆盖。
   新智元 `asi-post.html?id=...` 页面脚本已公开声明
   `/wp-json/wp/v2/posts/{id}?_embed=1` 正文接口，可作为无需登录的动态正文补取。
   保存原始JSON字节、接口URL、原文章URL和由 `content.rendered` 提取的正文；
   发布日期使用原始JSON的 `date`，不能用抓取时间代替。接口失败则继续保留pending。
3. 正常筛选近7天新发布内容。主线是中国科研/工程、AI真实应用、国产计算生态、
   昇腾/CANN与DeepSeek，以及华为产品具体场景。必须能讲清一个具体问题、
   人物或团队采取的行动、结果/阶段进展；至少3条新事实有精确原文证据。
   海外资讯只在确实服务主线时采用。拒绝纯煽动标题、传言、换皮旧闻、抽象榜单。
   历史案例和计划节点保留真实时间，发布日期不能冒充事件发生时间。
   用户2026-10-03明确否定“AI任务停了又开，消息为什么卡在队列里”：软件更新日志、
   小功能和Bug修复列表缺乏观众价值，不进入视频候选。至少3条事实是证据门槛，
   不是故事成立的证明。先确认具体科研/工程/使用场景中的真实问题、团队实际行动、
   可感知的结果或阶段进展，再核事实。不能用虚构开发者读公告、整理卡片、准备测试，
   补足原始报道没有的应用行动和结果。软件内容只有附带具体应用实践或工程攻关故事时
   才考虑；版本修复记录可留作研究背景。宁缺毋滥，没有合格故事就不新增。
4. 先核对旧 `research/deadline30_20261002/catalog_?.json`、其他已有catalog及
   本素材 `inbox/`。用 `core.content_id/fact_id/deduplicate` 检查同事件和事实；
   同一事件多来源合并证据，不能改标题或主体写法逃过去重。既有不可变包需要
   补充证据时另存sidecar，不能覆盖。仅凭去重程序无命中不代表主题一定新。
5. 每轮最多处理3个真正合格的新故事。创建完整 `package.schema.json` 素材包：
   来源HTML/正文/hash/准确日期、带精确证据跨度的事实、原创旁白、因果递进镜头计划、
   实际媒体文件/hash/许可状态、时码和关键帧。正式事实以原文为准；原片有语音时
   可用已缓存Whisper做ASR，专名和数字未复核须保持 `automatic_unreviewed`。
   只生成与故事匹配的原创概念示意或使用许可明确的素材。原站照片/原片留作研究，
   不默认可生产使用。`build_batch.render(spec,out)`可用于适合的示意补充片段，
   不能把它的通用图形套成所有题材的成片；不要重跑固定首批4故事冒充新增。
6. 调用 `core.enqueue_candidate(package,material_root,legacy_records(...))` 写入文件
   候选inbox。用 `core.validate` 和JSON Schema验证，检查视频可解码、画面和运动；
   未完成旁白合成、字幕对齐及主管线叙事/结构审核时保持 `production_ready=false`。
   运行 `.venv-workflow/bin/python -m workflow.material_pipeline.refresh_feed` 累计更新
   `candidate_feed.json`、`handoff.json`，保留此前全部候选，写入素材侧 `progress.json`。
   用户在2026-10-03明确要求“把这一步接通”：已启用
   `research/material_pipeline/production_bridge.json`，refresh_feed同时把候选同步到既有
   生产库的素材接收与编导入口；候选集合未变化时也继续处理尚未审稿的条目。
   每轮最多推进2份（硬上限3份），核验包/来源hash、精确事实、日期和去重，
   使用既有模型逐条审稿，再调用主管线现有batch/plan编导，复审最终manifest。
   不把素材示意片段直接冒充已审核成片；生成真实、绑定manifest hash的关键帧请求。
   有合适原创/已许可片段时，可另写 `production_drafts/<content_id>/plan.json`：
   包含不可变package文件hash、真实作者及标准manifest草稿。用现有plan.validate核验；
   逐镜绑定实际包内片段、hash、合法时码、真实预览帧及完整剧情状态，写原创旁白和
   语义字幕分行，保留最后至少2秒配乐。明确概念示意与厂商报告边界，不把假设写成
   用户成功案例或独立实测。适配器仍做真实模型的逐条事实和最终叙事审核；草稿不得
   自带审批/预检豁免。无合适草稿时使用现有API编导；无效结果隔离，不能强行通过。
   若完整叙事需要主管线制作新场景，草稿可显式设 `media_mode=main_generated_scenes`：
   每镜仍需可行动作、完整连续状态、来源归因和真实模型叙事审核；不得声称已有
   `source_path`、`previsual_path` 或 `reference_images`，不得夹带已生成/预检状态。
   原创示意素材保留为机制补充，不能替代全部场景。适配器仅生成缺失首尾帧的制作
   请求；主管线负责实际画面、配音、整板审核与预检。虚构角色和演绎必须明确披露，
   不把未验证能力拍成真实产品成功运行、脑控设备成功或临床效果。
   查看 `production_status.json` 和 `output/production/material_intake/status.json`。
   若刷新中断，下一轮先用 `--limit 0` 检查接收状态；有attempt的外部调用不盲目重试。
   显式恢复须先只读查清所有远端调用终态、归档并核验hash，写对应
   `output/production/material_intake/<content_id>/resume_audit.json`，再使用
   `production_bridge --resume <content_id>`。不得只改数据库状态自动重放。
7. 保存本轮 `decisions.json`：选中、重复、无关、证据不足、待复核和故障的理由及
   证据路径。只有明确排除的文章才在 `monitor/state.json.seen_articles` 标记
   `rejected/duplicate/outside_window`；待复核保留 `pending`，不得当作已完成素材。
   用户否定的候选用 `editorial_dispositions.json` 记录 `withdrawn`、包文件hash与理由，
   保留不可变原件供审计。`refresh_feed`排除撤回项；未开编导且没有制作请求的对应
   `material_intakes`/`editorial_backlog`设为`withdrawn`，不能下一轮刷新重新准入。

## 交接与运行边界

2026-10-09 02时轮次：素材适配器对新接收行以及现有编导器的隐式终态更新，
均在写入前使用既有`begin_immediate`作有限写锁获取重试，不重跑模型或编导操作。
实际SQLite争用及无重复入队回归检查通过28项；持续占锁仍会明确失败，不能当作
生产成功或修改主管线调度器来绕过。新CANN素材
`material_fd0462791f704999ab4bf997`已完成来源v2审核，远端终态completed/pass
已归档；本地原创草稿编导因共享库写锁失败，尚无最终叙事审核或制作请求。
本轮一次显式恢复复用了原来源审核，仍被持续写锁阻断，状态material_attention。
原模型回执、写锁检查、恢复记录见`monitor/runs/20261009T021701_915183/`。
后续先查本地durable attempt、最新失败和所有外部终态；写锁未解除不继续恢复。
不要重复调用已完成来源审核；不能只改状态或拿本地manifest冒充已审制作请求。

来源正文没有自身日期时，可使用同发布方官网索引中同标题、同原始链接的独立
`publication_index`证据：保存索引原始HTML与解析正文/hash，绑定一个真实article
元素的精确字符跨度、可见日期和标题。校验同发布方域名、链接身份及单条目日期，
拒绝跨条目拼日期、脚本/注释中的伪卡片或改写正文。该索引只证明公开日期，
不能替代原始全文事实证据，也不能把发布日期当全部实验发生日期。

2026-10-08 22时轮次修复另一交接缺口：一个事实在包内有多段精确引句，适配器
此前只向编导/最终叙事审核传第一段。现在保留原单段字段，并把每段分别经现有
`source_binding.bind`核验后的`evidence_quotes`与`evidence_bindings`全部传入。
不把非相邻段落拼成假引句，不改原包或模型结论，不把来源事实改成用户自述来
绕过审核。旧失败须核验所有远端终态后显式恢复，复用原来源审核；只有证据绑定
已改变的新manifest才做新的最终叙事审核。审计见对应轮次`multi_quote_adapter_validation.json`。

素材原件与采集记录仍只写 `research/material_pipeline/` 与 `workflow/material_pipeline/`。
用户新授权的接通例外：适配器可写既有 `queue.sqlite` 的 `material_intakes`、
`editorial_backlog` 和与本次素材对应的 `editorial_items`，制作文件写
`output/production/material_intake/`。通过素材与叙事审核的条目处于
`awaiting_storyboard`，配有 `production_request.json`；这代表已进入主管线编导，
仍需实际配音、首尾关键帧、整板审核与粗剪预检，才能由唯一主管线接纳视频任务。
不改jobs/tasks、唯一调度器、审批、既有主manifest、30条返修控制及主continuation，
不提交GPU/NPU任务或另建生产控制器。接收和编导状态不等同于成片或已获审批。

无合格新增且生产接收状态未变化时保持安静。只有交付了新素材包、
素材首次通过编导并形成制作请求、关键来源出现新的持续故障、或需要
用户提供缺失登录/访问条件时通知；单次失败记录并在后续重试，别每轮重复报同一状态。

### 引句格式与编导失败的核验

2026-10-08确认一个本地误拦：模型把原文的不换行空格序列化成普通空格，
远端原始审核是pass，但逐字故事门把它改为revise。素材适配器现在保留完整原始
模型回应，再只对唯一、仅空白差异的引句回绑真实原文字节；词语、标点、来源和
模型原结论不得修改，严格逐字门仍执行。`source_quote_binding.json`绑定不可变包、
正文hash、原回应及改动列表。旧误拦须先核验远端原始pass和全部终态，再使用明确
恢复入口复用审核；不能人工把模型拒绝改成通过，也不能重放已完成审核。

新SkillPoison候选`material_6f7cd5903edd7161550bcfdc`的首次编导已确认返回无效JSON，
处于material_attention，无制作请求。终态、原始输出及失败说明保存在该素材接收
目录的`planning_failure_audit.json`及对应采集轮次；该原始输出还含未审演绎和字幕
结构问题，不能通过修几个括号就冒充合格manifest。后续需要忠实来源的完整草稿与
真实叙事审核，恢复前重新核验成功审核及失败编导回执，不盲目重放原模型任务。

2026-10-08 20时轮次已完成上述SkillPoison修订：先只读核验远端旧审核
completed/pass与旧编导failed/invalid_json，归档终态后，通过显式恢复复用原审核。
`production_drafts/material_6f7cd5903edd7161550bcfdc/plan.json`为48秒、5镜完整原创
讲解草稿；新的真实模型最终叙事审核已pass，首次形成对应接收目录的
`production_request.json`，状态`awaiting_storyboard`、`production_ready=false`。
审计见`monitor/runs/20261008T201803_417680/decisions.json`及`external_audit.json`。
不再重放旧审核、旧编导或本次已完成叙事审核；实际画面、配音和预检仍由主管线负责。
同轮量子位502032页面标题及正文出现异常`hacked by`内容，仅将该单页研究隔离为
pending并定期重查；不据此断言整站入侵，也不向候选或模型传入该异常正文。

## 主管线自动生产接收（2026-10-03新授权）

用户明确要求“让现有主管线自动接收已审素材，执行生产准备，再交给现有调度器开工”。
`workflow.material_production.tick` 已接在现有 `scripts/run_user_repair30.py` 唯一控制循环，
读取 `output/production/material_intake/production_policy.json`。该主管线负责生产准备及
接纳视频任务；素材采集对话仍只交付制作请求，不另起控制器。

每份请求及全部证据按hash绑定，最多两个有限准备子进程：整段自然配音与真实字词时间、
按语义测量镜头时长、真实首尾图片、整板叙事复审及带真实声音的粗剪预检。
图片独立生成须显式声明共享场景规范、未施加图片条件，并通过整板一致性审查；不得
默认图像已经连续。准备通过后，唯一主管线调用现有 `runner.submit`，加入现有
`render_jobs.json`，由现有调度器/工作池执行。旁白及配乐缓存与已审预检精确绑定，
调度器复核预检回执而不重复调用已完成模型。成片仍须质量检查及人工审批，不能自动发布。

实际状态看 `output/production/material_intake/production/state.json`，每份版本下保存
`worker_attempt.json`、`worker_status.json`、语音/图片回执、`preflight/acceptance.json`
和 `result.json`。`preparing` 是已被主管线接收并执行准备；`rendering` 才表示已交现有
视频调度器；`ready_for_human_review` 等待人工审批。失败或提交结果不明转 `attention`，
核验持久化回执后才可显式恢复，不把失败或旧请求重新自动排队。新审核版本单独接收，
原请求、包和成片审批保持不变。未来通过审核的素材自动承接，主循环保留常驻。

### 10月9日新增候选的审核与修订

2026-10-09 14时轮次：VPP2 `material_860760112b6dfe81c43d636c` 与临界拓扑
`material_d85caeb3a1a14fb152f24f13` 的来源v2均已真实模型pass。VPP2原最终
叙事审核revise：整片同一板上的圈选/卡片发光没有呈现团队行动。两份完整草稿已
改为数据整理/训练/操作测试或声学测量/二维实验的原创流程演绎，机制图仅一镜；
没有修改包、原审核结论或旧manifest，也没有实际生成新画面/配音。旧稿与新版hash
见`monitor/runs/20261009T142222_517061/draft_revisions.json`。新版仍须最终叙事审核。

临界拓扑本地编导写锁失败，VPP2完成旧稿审核后待修订，均为material_attention、
无制作请求。全部远端终态已只读核验；对应接收目录已保存resume_audit。当前
有限写锁探测仍失败，未显式恢复；下轮先核验最新attempt、已归档终态和写锁。
写锁解除后才可显式恢复这两份，复用来源pass、使用新草稿的新manifest做最终
叙事审核；禁止重放已完成旧来源/旧叙事审核。保留每轮最多2份不同素材的推进范围。
