# 电子取证标准 RAGFlow 知识库

把电子取证领域的国家标准（GB/T）、公共安全行业标准（GA/T）和司法鉴定技术规范
（SF/Z、SF/T）建成一个**可检索、可回溯**的本地知识库：用 RAGFlow 负责解析与检索，
用硅基流动的嵌入、重排和生成模型负责问答，目标是每条回答都能落到具体标准编号和条款位置。

仓库保存的是**部署与管理方案**——部署配置、导入与校验工具、标准元数据格式、
验收记录，以及实施过程中踩到的坑。**标准正文不在仓库内。**

---

## 当前状态

> 更新于 2026-09-15

| 项目 | 状态 |
| --- | --- |
| 运行环境 | RAGFlow v0.27.1 + Elasticsearch 8.11.3 + MySQL 8.0.40 + MinIO + Valkey（Docker Desktop / WSL2） |
| 正文库 | 「电子取证国家及行业标准」，2 份正文：SF/Z JD0400001-2014（61 切片）、SF/Z JD0400002-2015（9 切片） |
| 目录库 | 「电子取证标准目录（候选）」，85 张标准卡片、89 切片（**仅元数据，不含条款**） |
| 对话助手 | 「电子取证标准问答」，生成模型 Qwen3-30B-A3B-Instruct-2507，重排 bge-reranker-v2-m3，绑定上述两个知识库 |
| 引用合规 | 同一问题连续 5 次，5/5 均给出「标准编号 + 条款号」且无 `[ID:x]` 标记 |
| 端到端问答 | 抽样 4 类问题全部给出正确条款依据；证据不足类问题正确拒答 |
| 场景匹配 | AC 抽样：AC-03、AC-24 通过；AC-01、AC-13 部分通过；AC-05、AC-08 未通过 |

**进行中**：其余 13 份已取得正文的批量导入（因容器内存受限一度阻塞，已通过降低
Elasticsearch 堆内存缓解）；候选标准的官方状态核验（决定 AC 用例能否按设计判定）。

## 架构

```text
浏览器 ── http://127.0.0.1:8081
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
  06-标准总目录候选.jsonl            76 条候选标准目录（仅元数据）
  11-标准卡片索引/                   85 张标准卡片，用于目录库上传
  05-转换质量核验报告.md             转换质量抽查结论
  09-检索验收记录.md                 检索层与端到端验收结果
  13-场景匹配验收记录.md             场景匹配验收与卡点
  15-批量导入记录.md                 批量导入流程与阻塞点
  build_import_inventory.py          生成元数据卡片、切片关联与哈希台账
  build_standard_cards.py            合并候选目录与详细卡片，生成标准卡片
  clean_markdown.py                  去除目录点线、重复页眉与页码
  convert_pdf_reading_order.py       版式转换失效时，按阅读顺序重建正文
  check_text_quality.py              对比转换版本并择优
  validate_import.py                 零依赖资产校验器

电子取证标准RAGFlow最小可用版方案书.md   完整方案与执行记录
场景分类、标准标签与验收用例设计.md       场景字段、标签词表与 AC-01～AC-24 验收用例
后续改进方案.md                          按优先级排列的后续工作
```

以下内容**只保留在本地**，已被 `.gitignore` 排除：
`01-正文Markdown/`、`01-正文Markdown-清洗版/`、`01-正文Markdown-重转/`、
`03-正文切片关联.jsonl`（其 `section` 字段含条款原文）。

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

## 已知限制

- **候选标准全部为「待权威核验」**，按验收规则不得作为主推荐，
  而 AC-01～AC-12 的核心判定项正是主推荐，因此这套用例尚不能按设计判定。
  这是数据核验问题，不是检索问题。
- 目录缺少 AC 用例涉及的 `GA/T 2160-2024`、`GA/T 825-2009`。
- 场景匹配的召回只做了抽样（6 个用例），未跑完整 24 个。
- 条款级结论只在转换质量合格的文件上成立；同一批标准里仍有转换物不合格的条目。

## 后续路线

按优先级排列，详见 [后续改进方案.md](后续改进方案.md)：

1. ~~引用格式收敛~~（已完成）
2. ~~建立标准卡片索引~~（索引已建成，验收目标受核验状态所限）
3. **批量导入其余 13 份已取得正文**（进行中）
4. 检索参数调优（阈值、向量权重、重排）
5. 环境稳定性与自动化恢复
6. 验收自动化（一条命令产出验收报告）
7. 仓库与合规策略

## 内容边界

仓库**不包含**：

- 标准正文（原始 PDF、转换后的 Markdown、清洗版与重转版）。
- 切片关联明细 `03-正文切片关联.jsonl`，其 `section` 字段含条款原文。
- API Key、数据库密码与其他部署凭据。
- 案件材料、个人信息与来源不明的文件。

仓库**包含**标准编号、名称、状态、官方来源 URL、哈希与质量指标；
元数据卡片与哈希台账中保留了**相对路径**（相对本地库根目录）。

标准文本的著作权属于发布机构，使用与传播请遵循来源授权。

## 许可

脚本与文档采用 MIT 许可，见 [LICENSE](LICENSE)。
