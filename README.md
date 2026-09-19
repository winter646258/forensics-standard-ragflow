# 电子取证标准 RAGFlow 知识库

把电子取证领域的国家标准（GB/T）、公共安全行业标准（GA/T）和司法鉴定技术规范
（SF/Z、SF/T）建成一个**可检索、可回溯**的本地知识库：用 RAGFlow 负责解析与检索，
用硅基流动的嵌入、重排和生成模型负责问答，目标是每条回答都能落到具体标准编号和条款位置。

仓库保存的是**部署与管理方案**——部署配置、导入与校验工具、标准元数据格式、
验收记录，以及实施过程中踩到的坑。**标准正文不在仓库内。**

---

## 当前状态

> 更新于 2026-09-19

| 项目 | 状态 |
| --- | --- |
| 运行环境 | RAGFlow v0.27.1 + Elasticsearch 8.11.3 + MySQL 8.0.40 + MinIO + Valkey（Docker Desktop / WSL2） |
| 正文库 | 「电子取证国家及行业标准」，**15 份正文 / 424 切片 / 36,064 tokens**（全部解析成功） |
| 目录库 | 「电子取证标准目录（候选）」，**87 张标准卡片 / 87 切片**（**仅元数据，不含条款**）；2026-09-19 全量重建 |
| 对话助手 | 「电子取证标准问答」，生成模型 Qwen3-30B-A3B-Instruct-2507，重排 bge-reranker-v2-m3，绑定上述两个知识库 |
| 引用合规 | 同一问题连续 5 次，5/5 均给出「标准编号 + 条款号」且无 `[ID:x]` 标记 |
| 端到端问答 | 抽样用例全部给出正确条款依据；证据不足类问题正确拒答（见 `ragflow-import/16-验收报告.md`） |
| 场景匹配 | AC 抽样：AC-03、AC-24 通过；AC-01、AC-13 部分通过；AC-05、AC-08 未通过 |
| 查询应用 | `ragflow-query/`，双击 `启动查询.bat` 即用；6 条端到端用例引用 6/6、回答 6/6 |

**进行中**：候选标准的官方状态核验（决定 AC 用例能否按设计判定）；
目录库卡片的「正文状态」字段需随核验结果回写（现仍标「未获取」，与实际不符）。

## 架构

```text
浏览器 ── http://127.0.0.1:8899          ← 查询应用（ragflow-query/，可双击启动）
            │
            │  /api/retrieve（约 0.5 s，出引用） + /api/chat（约 30 s，出回答）
            ▼
浏览器 ── http://127.0.0.1:8081          ← RAGFlow 自带界面（管理用）
            │
        RAGFlow（Docker）
        ├── 正文库：标准正文切片（可给出条款号与条款内容）
        ├── 目录库：标准卡片元数据（只能给出编号、名称、状态）
        └── 对话助手：检索 → 重排 → 生成，带引用约束的提示词
            │
        MySQL / Elasticsearch / MinIO / Valkey
            │
        硅基流动 API
        ├── Embedding  Pro/BAAI/bge-m3
        ├── Rerank     Pro/BAAI/bge-reranker-v2-m3
        └── LLM        Qwen/Qwen3-30B-A3B-Instruct-2507
```

查询应用**并行调用**检索与生成两个接口：检索约 0.5 秒先返回引用，
回答约 30 秒后到达。助手关闭引用面板（`quote=false`）导致
`chat/completions` 的 `reference` 恒为空，因此引用单独走 `/api/retrieval` 取。

两个知识库物理分离是刻意的：目录库只有编号与状态，**绝不能**用来回答条款内容；
正文库只放已核验的正文，**不混入**仅有目录的候选标准。

## 目录结构

```text
ragflow-deploy/                    部署配置与运维脚本
  docker-compose.override.yml        官方 Compose 的本地覆盖（回环端口、容器 DNS、ES 堆上限）
  compose.ps1                        启停入口，自动加载 .env.local
  start-docker.ps1                   引擎无响应时的恢复脚本
  .env.example                       变量清单（占位值，不含真实密码）
  README.md                          部署与排障说明

ragflow-import/                    导入资产与工具
  02-元数据卡片/  *.jsonl            逐标准元数据卡片与批量索引
  04-哈希台账.jsonl                  原始 PDF 与转换物的大小、SHA-256、来源
  06-标准总目录候选.jsonl            78 条候选标准目录（仅元数据，14 条已核验官方状态）
  11-标准卡片索引/                   87 张标准卡片，用于目录库上传
  05-转换质量核验报告.md             转换质量抽查结论
  09-检索验收记录.md                 检索层与端到端验收结果
  13-场景匹配验收记录.md             场景匹配验收与卡点
  15-批量导入记录.md                 批量导入流程、阻塞点与完成情况
  16-验收报告.md                     最近一次「库存 + 检索 + 端到端问答」验收产物
  build_import_inventory.py          生成元数据卡片、切片关联与哈希台账
  build_standard_cards.py            合并候选目录与详细卡片，生成标准卡片
  clean_markdown.py                  去除目录点线、重复页眉与页码
  convert_pdf_reading_order.py       版式转换失效时，按阅读顺序重建正文
  check_text_quality.py              对比转换版本并择优
  validate_import.py                 零依赖资产校验器
  import_texts_to_ragflow.py         正文批量导入（幂等；分批解析 + 批内失败单发重试）
  rebuild_catalog.py                 目录库重建（先清后传，避免同名产生重复副本）
  verify_knowledge_base.py           一键验收：库存核对 + 检索命中 + 端到端问答引用合规
  17-AC标准状态核验.md               14 条候选标准的官方状态核验记录（含来源 URL）
  18-目录库重建记录.md               目录库重建的差异分析、执行过程与遗留

ragflow-query/                     查询应用（可交付给使用者）
  启动查询.bat                       双击启动：查 Python → 探活知识库 → 拉起页面
  app.py                             后端，单文件、仅用标准库；内置上游抖动自动重试
  accept_query.py                    一键端到端验收，产出 logs/验收记录.md
  config.json                        端口、助手与知识库 ID、检索参数
  static/index.html                  查询界面
  logs/验收记录.md                   6 条端到端用例的完整记录
  使用说明.md                        面向使用者的说明

电子取证标准RAGFlow最小可用版方案书.md   完整方案与执行记录
场景分类、标准标签与验收用例设计.md       场景字段、标签词表与 AC-01～AC-24 验收用例
后续改进方案.md                          按优先级排列的后续工作
```

以下内容**只保留在本地**，已被 `.gitignore` 排除：
`01-正文Markdown/`、`01-正文Markdown-清洗版/`、`01-正文Markdown-重转/`、
`03-正文切片关联.jsonl`（其 `section` 字段含条款原文）、
查询应用的运行日志与目录库回滚备份。

## 快速开始

### 1. 部署 RAGFlow

需要可用的 Docker Engine，以及官方 RAGFlow 仓库（脚本默认
`D:\RAGFlow\ragflow\docker`）。本仓库只提供本地覆盖，不修改官方文件。

```powershell
cd ragflow-deploy
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action config
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action up
```

`.env.local` 保存本次部署的随机密码，**重建容器必须使用同一个文件**，
否则新密码与数据卷里已保存的不一致，RAGFlow 会连不上 MySQL。

引擎无响应时（本机出现过多次）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-docker.ps1
```

### 2. 校验导入资产

```powershell
python ragflow-import\validate_import.py `
  --pdf-root "<本地库根目录>" `
  --report ragflow-import\07-导入资产验收.json
```

只用 Python 标准库，检查元数据、切片关联、Markdown 哈希、行号范围与转换质量报告；
指定 `--pdf-root` 时一并复核原始 PDF 的大小与 SHA-256。

### 3. 准备正文

```powershell
python ragflow-import\clean_markdown.py           # 生成 01-正文Markdown-清洗版/
python ragflow-import\check_text_quality.py --pdf-root "<本地库根目录>" --apply
```

清洗只删除目录点线、重复页眉和页码，不改动条款文字。
`check_text_quality.py` 会对比「现有转换物」与「按阅读顺序重转版」并择优——
注意重转**不是通用更优解**，判据与反例见下文「关键发现」。

生成元数据卡片与哈希台账的 `build_import_inventory.py` 需要知道本地库位置，
该位置通过环境变量传入，脚本里不写死本机路径：

```powershell
$env:FORENSICS_VAULT_ROOT = "<Obsidian Vault 根目录>"
python ragflow-import\build_import_inventory.py
```

### 4. 生成标准卡片并建目录库

```powershell
python ragflow-import\build_standard_cards.py --report ragflow-import\12-标准卡片索引清单.json
```

生成的 `11-标准卡片索引/` 上传到独立知识库。**新建知识库必须显式关闭 RAPTOR 与
GraphRAG**，否则解析会因追加的 LLM 调用压垮容器：

```json
{"parser_config": {"raptor": {"use_raptor": false}, "graphrag": {"use_graphrag": false}}}
```

### 5. 启动查询应用

```bat
cd ragflow-query
启动查询.bat
```

双击或命令行启动均可。脚本会依次检查 Python、探活知识库（不在线则调起
Docker Desktop 并等 45 秒）、拉起服务并打开浏览器，默认地址
<http://127.0.0.1:8899/>。详见 [ragflow-query/使用说明.md](ragflow-query/使用说明.md)。

只用 Python 标准库，无需 `pip install`。API Token 默认从 Docker 里的
MySQL `api_token` 表自动读取，不必手工填。

## 关键发现

以下几条都是本机实测得到的，也是这个仓库相对方案文档更有价值的部分。

1. **RAPTOR 与 GraphRAG 默认开启会压垮环境。** 新建知识库不带 `parser_config` 时
   两者默认 `true`，每份文档额外跑聚类摘要与实体抽取。85 份小卡片在开启状态下解析缓慢、
   模型接口频繁中断、Docker 引擎连续进入 500 状态；关闭后剩余 16 份**数秒完成**。

2. **Elasticsearch 不设堆上限会吃掉一半以上内存。** ES 镜像默认按容器内存上限
   自动定堆，实测在 7.5 GB 上限下占用 4.43 GB，挤垮同机的 RAGFlow。
   固定 `ES_JAVA_OPTS=-Xms1g -Xmx1g` 后降到 1.64 GB，容器可用内存从 0.5 GB 回到 4.2 GB。

3. **生成模型不能用思考型。** 思考型模型把输出额度消耗在 `reasoning_content` 上，
   而 RAGFlow 只读取 `content`，表现为空回答。RAGFlow 虽有 `enable_thinking=false`
   的处理，但经 SiliconFlow 这条链路未生效，改用非思考型 instruct 模型后正常。

4. **RAGFlow 会在系统提示词之后追加自己的引用规则**（`citation_prompt`），
   要求用 `[ID:i]` 标注来源，会盖过助手提示词里的格式要求。
   只改提示词无效；最终通过关闭助手的 `quote` 选项去掉该追加规则，
   并在提示词中要求「标准编号 + 条款号」与末尾的「依据」小节。

5. **按版式转换会把部分标准拆成表格碎片。** 受害文件表现为切片数异常少、
   条款不成句。这类文件按阅读顺序重新提取文本层可以修复（实测切片 2 → 9）；
   但结构正常的文件用同样方法反而会引入新噪声（标准编号被插空格、页脚混入正文），
   所以必须按文件择优，不能一概重转。

6. **重建容器必须带 `.env.local`。** 该文件保存本次部署的随机密码；
   用官方默认密码重建，会与数据卷中已保存的密码不一致，RAGFlow 直接连不上 MySQL。

7. **双库检索需要单独标定阈值。** 目录卡片对场景类问题的相似度约 0.44～0.51，
   沿用正文库的阈值会被挡住；把助手阈值降到 0.1 后即命中。

8. **并发解析每批必挂 1 份，单发重试必过。** 4 份一批时总有 1 份落到 `run=FAIL`，
   把该份单独重试立即成功。这是 embedding 端的**并发限流，与文本内容无关**；
   `import_texts_to_ragflow.py` 据此内置「批内失败 → 单独重试」。

9. **embedding 偶发 `RemoteDisconnected`，重发即成功，不必重启容器。**
   报 `RemoteDisconnected('Remote end closed connection without response')`，
   多在容器连续运行约 30 分钟后出现。容器内同环境用 `requests`、`curl`、
   真实密钥、空密钥逐一测试**全部正常**，说明网络、密钥、依赖、代码都无问题。
   后续实测（2026-09-19）更正了先前「必须重启容器」的判断：对同一问题连续重发
   3 次**全部成功**，容器运行时长未变；端到端验收中失败的那一条也是重发即过。
   因此这是**上游连接的偶发抖动**，正确做法是**应用层退避重试**
   （`ragflow-query/app.py` 内置 2 次重试、退避 2+4 秒），`docker restart` 只作最后手段。

10. **`/api/v1/chat/completions` 只认 `chat_id`，不认 OpenAI 风格的 `model`。**
   传 `model` 时 `chat_id` 为空，服务端静默回退到「默认对话」分支——
   该分支没有知识库、没有重排、模型换成租户默认 `Qwen3.5-9B`，
   于是一个参数错误同时造成「引用恒为空 + 回答乱猜标准编号 + 单次耗时 99～263 s」。
   改用 `{"chat_id": ..., "question": ...}` 后耗时降到 9.9 s，并能正确引用条款。

11. **Windows 可能把 8081 划进 Hyper-V/WSL 的保留端口段，症状很有迷惑性。**
   容器是 `Up` 状态，`HostConfig.PortBindings` 里也明明写着 `127.0.0.1:8081`，
   但 `NetworkSettings.Ports` 是空的，8081 连不上，日志里没有任何报错。
   直到强制重建容器才吐出真正的原因：

   ```text
   ports are not available: exposing port TCP 127.0.0.1:8081 -> 127.0.0.1:0:
   listen tcp4 127.0.0.1:8081: bind: An attempt was made to access a socket in a way
   forbidden by its access permissions.
   ```

   `netsh int ipv4 show excludedportrange protocol=tcp` 一看，8081 落在
   `7937–8136` 这类保留段里 —— **没有进程占用，是系统禁止绑定**。
   这些保留段由 Hyper-V/WSL 在初始化时动态划分，会随重启游走，
   所以同一个端口昨天能用、今天突然不行。

   ```powershell
   net stop winnat
   net start winnat
   ```

   重启 `winnat` 会重新划分保留段（实测 `7937–8136` 整段被释放），随后
   `docker start <ragflow 容器>` 即可恢复。**不用改端口，也不用重建知识库。**

12. **重建 RAGFlow 容器会轮换 API Token。**
   查询应用如果只在启动时读一次 Token，容器一重建就全部请求 401 ——
   界面看着正常、却什么都查不出来，还以为知识库坏了。
   `ragflow-query/app.py` 因此在命中 401 时自动重读一次 Token 并重发
   （只重发一次，凭证真的不对时不会反复撞墙）。

## 已知限制

- **候选标准多数仍为「待权威核验」**（64/78），按验收规则不得作为主推荐，
  而 AC-01～AC-12 的核心判定项正是主推荐，因此这套用例仍不能全部按设计判定。
  已核验的 14 条见 `ragflow-import/17-AC标准状态核验.md`。
  这是数据核验问题，不是检索问题。
- ~~目录缺少 AC 用例涉及的 `GA/T 2160-2024`、`GA/T 825-2009`。~~
  （**已补齐**：2026-09-19 重建目录库时一并加入，`GA/T 825-2009` 经核验为**废止**，
  见 `ragflow-import/18-目录库重建记录.md`）
- **目录库卡片的「正文状态」字段已过期**：15 份标准已进入正文库，卡片却仍标「未获取」，
  实测会让模型在回答里声称"知识库中标准正文均未获取"。需随状态核验一并回写。
- 场景匹配的召回只做了抽样（6 个用例），未跑完整 24 个。
- 条款级结论只在转换质量合格的文件上成立；同一批标准里仍有转换物不合格的条目。

## 后续路线

按优先级排列，详见 [后续改进方案.md](后续改进方案.md)：

1. ~~引用格式收敛~~（已完成）
2. ~~建立标准卡片索引~~（索引已建成，验收目标受核验状态所限）
3. ~~批量导入其余 13 份已取得正文~~（**已完成：15 份 / 424 切片**）
4. 检索参数调优（阈值、向量权重、重排）
5. 环境稳定性与自动化恢复
6. ~~验收自动化~~（**已具备**：`verify_knowledge_base.py` 一条命令产出 `16-验收报告.md`）
7. ~~可用的查询界面~~（**已完成**：`ragflow-query/` 双击即用，6 条端到端用例全通过）
8. ~~目录库数据同步~~（**已完成**：87 张卡片全量重建，含新增 2 条与核验后的状态）
9. 仓库与合规策略

## 内容边界

仓库**不包含**：

- 标准正文（原始 PDF、转换后的 Markdown、清洗版与重转版）。
- 切片关联明细 `03-正文切片关联.jsonl`，其 `section` 字段含条款原文。
- API Key、数据库密码与其他部署凭据。
- 案件材料、个人信息与来源不明的文件。

仓库**包含**标准编号、名称、状态、官方来源 URL、哈希与质量指标；
元数据卡片与哈希台账中保留了**相对路径**（相对本地库根目录）。

**验收报告按同一口径脱敏。** `verify_knowledge_base.py` 与 `accept_query.py`
默认只把标准编号、条款号、相似度与判定结果写进报告——能证明「命中了正确标准与
正确条款」的正是编号与条款号本身；命中的条款原文与含条款原文的回答正文都被移除。
需要含原文的版本时加 `--with-clause-text`，**该版本只留本地，不要提交**。

标准文本的著作权属于发布机构，使用与传播请遵循来源授权。

## 许可

脚本与文档采用 MIT 许可，见 [LICENSE](LICENSE)。
