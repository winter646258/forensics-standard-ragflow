# RAGFlow 本地部署配置

此目录是对 `D:\RAGFlow\ragflow\docker` 官方 Compose 配置的本地覆盖，
不修改官方仓库文件。

- 固定镜像：`infiniflow/ragflow:v0.27.1`
- 服务：RAGFlow CPU、Elasticsearch、MySQL、MinIO、Redis
- 访问地址：`http://127.0.0.1:8081`
- 仅 Web 入口绑定宿主机回环地址；数据库、搜索、对象存储和缓存不向宿主机开放端口。
- 官方 Compose 目录中的 `.env` 保存真实密码；本目录的 `.env.example` 只是字段参考，
  不会被脚本自动加载，也不得把真实 `.env` 复制到 Git 仓库。

官方 README 的最低要求是 4 CPU、16 GB RAM、50 GB 磁盘。本机 Docker Engine
资源不足时仅进行受限的本地 MVP 验证；若容器频繁重启或内存不足，应先提高 Docker
可用内存，再继续导入和解析。

## 使用

如果 Docker Desktop 启动后引擎不响应，先运行恢复脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-docker.ps1
```

它会结束残留的 Docker Desktop 进程、重启 WSL 虚拟机、把无法删除的悬空套接字目录
改名归档（不会删除任何镜像或知识库数据），再启动 Docker Desktop 并等待引擎就绪。

引擎正常后，在 PowerShell 中从本目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action config
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action up
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action ps
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action logs
powershell -ExecutionPolicy Bypass -File .\compose.ps1 -Action down
```

`config` 用于启动前检查最终 Compose 配置；`down` 只停止并移除容器，刻意不删除
数据卷。需要下载镜像时单独执行 `-Action pull`。不得绕过脚本手工创建 MySQL、
Elasticsearch、Redis 或 MinIO。

## 导入资产检查

在上传 Markdown 前执行：

```powershell
python ..\ragflow-import\validate_import.py `
  --report ..\ragflow-import\07-导入资产验收.json
```

校验器只使用 Python 标准库，检查元数据、切片关联、Markdown 哈希、行号范围和
转换质量报告；源 PDF 位于 Obsidian Vault 时，再通过 `--pdf-root` 指定 Vault 根目录
进行 PDF 大小和 SHA-256 复核。
