# 电子取证标准 RAGFlow 知识库

面向电子取证国家标准、公安行业标准和司法鉴定技术规范的本地知识库方案。
用 RAGFlow 建库，用硅基流动的嵌入、重排与生成模型做检索问答，
目标是让每条回答都能回溯到具体标准编号和条款位置。

仓库保存的是**部署与管理方案**：部署配置、导入与校验脚本、标准元数据格式、
验收记录，以及过程中发现的问题。标准正文不在仓库内。

## 目录结构

```text
ragflow-deploy/          RAGFlow 本地部署配置与运维脚本
  docker-compose.override.yml  官方 Compose 的本地覆盖（仅暴露本地回环端口）
  compose.ps1                  启停入口，自动加载 .env.local
  start-docker.ps1             引擎无响应时的恢复脚本
  .env.example                 变量清单（占位值，不含真实密码）
ragflow-import/          标准导入资产与工具
  01-正文Markdown/             转换后的正文（本地保留，不入库）
  01-正文Markdown-清洗版/      实际用于上传的版本（本地保留）
  01-正文Markdown-重转/        从 PDF 按阅读顺序重建的版本（本地保留）
  02-元数据卡片/               逐标准元数据卡片
  02-元数据卡片.jsonl          批量索引
  04-哈希台账.jsonl            PDF 与 Markdown 的大小、哈希与来源
  06-标准总目录候选.jsonl      候选标准目录（不含正文）
  05-转换质量核验报告.md       转换质量抽查结论
  09-检索验收记录.md           检索层与端到端验收结果
  build_import_inventory.py    生成元数据卡片、切片关联与哈希台账
  clean_markdown.py            去除目录点线、重复页眉与页码
  convert_pdf_reading_order.py 版式转换失效时，按阅读顺序重建正文
  validate_import.py           零依赖资产校验器
电子取证标准RAGFlow最小可用版方案书.md   完整方案与执行记录
场景分类、标准标签与验收用例设计.md       场景字段、标签词表与 AC-01～AC-24 验收用例
```

## 快速开始

### 1. 部署 RAGFlow

需要先有可用的 Docker Engine 和官方 RAGFlow 仓库（默认路径
`D:\RAGFlow\ragflow\docker`）。本仓库只提供本地覆盖，不修改官方文件。

```powershell
cd ragflow-deploy
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action config
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action up
```

`.env.local` 保存本次部署的随机密码，**重建容器必须使用同一个文件**，
否则密码与数据卷里已保存的不一致，RAGFlow 会连不上 MySQL。
该文件已被 Git 忽略。

如果 Docker Desktop 启动后引擎不响应：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-docker.ps1
```

### 2. 校验导入资产

```powershell
python ragflow-import\validate_import.py `
  --pdf-root "<本地库根目录>" `
  --report ragflow-import\07-导入资产验收.json
```

校验器只用 Python 标准库，检查元数据、切片关联、Markdown 哈希、行号范围和
转换质量报告；指定 `--pdf-root` 时一并复核原始 PDF 的大小与 SHA-256。

### 3. 准备上传正文

```powershell
python ragflow-import\clean_markdown.py
```

生成 `01-正文Markdown-清洗版/`，用它上传。清洗只删除目录点线、重复页眉和页码，
不改动条款文字。

如果某份标准的转换物把正文拆成了表格碎片（切片数异常少、条款不成句），
用阅读顺序重建：

```powershell
python ragflow-import\convert_pdf_reading_order.py <标准.pdf> `
  --title "<标准名称>" --standard-id "<标准编号>" `
  -o "ragflow-import\01-正文Markdown-重转\<文件名>.md"
```

重建版本优先于机械清洗，`clean_markdown.py` 会原样采用。

## 已知问题

- **引用格式不稳定**：回答有时给出「标准编号 + 条款号」，有时只给出 RAGFlow 的
  `[ID:x]` 内部标记，需要收敛。
- **场景匹配验收尚未可判定**：AC-01～AC-24 期望命中的 GA/T、GB/T 标准
  正文与元数据卡片都还没入库，需要先建独立的标准卡片索引。
- **生成模型不能选思考型**：思考型模型会把额度消耗在 `reasoning_content` 上，
  而 RAGFlow 只读取 `content`，表现为空回答。当前使用非思考型 instruct 模型。
- **Docker Desktop 不稳定**：引擎会周期性返回 500/502，容器内 DNS 会短时失败。
  已通过指定公共 DNS 与恢复脚本缓解，属于环境问题。
- **转换质量决定可用性**：条款级结论只能在转换质量核验通过的文件上给出；
  表格线性化严重的文件必须先重转。

## 内容边界

仓库**不包含**：

- 标准正文（含原始 PDF、转换后的 Markdown、清洗版与重转版）。
- 切片关联明细 `03-正文切片关联.jsonl`，因为其中 `section` 字段含条款原文。
- API Key、数据库密码与其他部署凭据。
- 案件材料、个人信息与来源不明的文件。

仓库**包含**标准编号、名称、状态、官方来源 URL、哈希与质量指标，
以及本地路径信息（元数据卡片与哈希台账记录了本机库内路径）。

标准文本的著作权属于发布机构，使用与传播请遵循来源授权。

## 许可

本仓库的脚本与文档采用 MIT 许可，见 [LICENSE](LICENSE)。
