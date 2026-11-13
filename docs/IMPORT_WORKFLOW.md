# 文件导入流程

## 范围与节点

本次实现文件导入，不实现 `app/processor/query_process/` 中的检索流程。
统一入口是 `app/processor/import_process/agent/kb_import_workflow.py`，旧入口 `main_praph.py` 复用同一张图。

| 节点 | 输入与输出 |
| --- | --- |
| `node_entry` | 校验存在的 PDF、MD、Markdown 文件，生成导入标识和独立输出目录，设置路由 |
| `node_pdf_to_md` | 文字型 PDF 使用 PyMuPDF 提取文字与图片，扫描页使用 MinerU OCR；Markdown 输入跳过此节点 |
| `node_md_img` | 读取 Markdown，将本地图片上传 MinIO，并调用视觉模型生成描述；远程图片保留 |
| `node_document_split` | 按标题层级分节，再按段落、句子和字符边界切片，保留标题、父标题、来源及顺序 |
| `node_item_name_recognition` | 使用前几个切片调用文本模型，解析 JSON 中的主体名称；调用方可传 `item_name` 跳过识别 |
| `node_bge_embedding` | 分批生成切片和主体名称的 BGE-M3 稠密、稀疏向量，校验数量、维度和有限值 |
| `node_import_milvus` | 检查或创建两个集合，分批 upsert 切片和主体记录，全部成功后报告导入数量 |

## 准备与执行

使用项目已有的 Python 3.11 虚拟环境及依赖。以 `.env.example` 为参考配置本地 `.env`，至少检查：

- `OPENAI_API_KEY`、`OPENAI_API_BASE`、`ITEM_MODEL` 和有本地图片时使用的 `VL_MODEL`。
- `BGE_M3_PATH`、`BGE_DEVICE`；BGE-M3 默认维度为 `BGE_EMBEDDING_DIM=1024`，独立于云端 `EMBEDDING_DIM`。CPU 自动禁用 FP16。
- `MILVUS_URL`、`CHUNKS_COLLECTION`、`ITEM_NAME_COLLECTION`。两个集合名必须不同。
- 有本地图片时，配置 `MINIO_ENDPOINT`、访问凭据、`MINIO_BUCKET_NAME`；HTTPS 可设 `MINIO_SECURE=true`。
- `MD_ROOT_DIR`：输出根目录，默认 `./temp-files/`。每次完整导入创建新目录，不修改原文档。
- 文字型 PDF 使用已安装的 PyMuPDF，无需额外 OCR 模型；含扫描页的 PDF 使用本地 MinerU 模型及其 `magic-pdf.json` 配置，不调用 `MINERU_BASE_URL` 云端接口。OCR 相关模型及完整运行依赖须提前准备。

在仓库根目录执行一次导入：

```powershell
uv run python -m app.processor.import_process.agent.kb_import_workflow "path/to/manual.pdf"
uv run python -m app.processor.import_process.agent.kb_import_workflow "path/to/manual.md" --stream
```

也可使用已有环境，避免 uv 同步环境：

```powershell
.venv/Scripts/python.exe -m app.processor.import_process.agent.kb_import_workflow "path/to/manual.md"
```

流程会调用模型 API、上传图片并写数据库。输出目录包含 `processed.md`、`chunks.json`、`embeddings.json`，PDF 还会生成 `document.md` 和 `images/`。

## 存储约定与边界

切片集合使用 INT64 `chunk_id` 主键，以及 `document_id`、`content`、`title`、`parent_title`、`item_name`、`metadata`、`dense_vector`、`sparse_vector`。主体集合保存首个切片主键、文档标识、主体名称及两类向量。稠密向量使用 COSINE 索引，稀疏向量使用 IP 索引。

已有集合字段或维度不兼容时直接报错，不删除或重建历史集合。相同状态的入库节点可使用 upsert 重试；重新运行整个导入会创建新文档记录，不会自动去重。跨集合写入没有事务，失败可能留下部分数据，应保留状态后重试入库节点。

图片上传保持桶原有权限，不自动公开桶；文档保存持久的 `s3://bucket/object` 引用。需要展示时由调用方按权限生成访问链接。当前处理普通内联 Markdown 图片和 `<含空格路径>`，不解析 HTML 图片、引用式图片或远程图片内容。

目前仅支持普通切分，高级切分开关启用时明确报错。旧状态中的 API/vLLM 切分标记仅为兼容字段，不会启用额外模型服务。

## 验证记录

离线测试使用真实 LangGraph 执行 Markdown 和 PDF 两条路径，替换 PDF 解析引擎、模型请求及存储边界，不请求真实服务：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_import_workflow.py -v
```

测试覆盖路由、重复图片、原文保护、标题与代码块、切片长度、向量维度错误、无效主体响应、PDF 转换失败、存储失败、任务状态清理及真实 Milvus SDK schema 构建。已确认本机 MinerU 和 BGE 适配器可导入。

实现时修正了静态路由多余的 `self` 参数、重构后的包导出、LLM 地址变量名不一致，以及 MinerU 默认禁用内置模型导致退出的问题。MinerU 旧版本依赖会产生 `fitz` 弃用警告。

最初实现阶段仅做离线验证；本次真实文件验收结果见下节。扫描 PDF 的 MinerU OCR 和成功的 LLM/视觉生成仍未验证。


## 真实资源验收（2026-11-13）

选用 `resource/doc/H3C LA2608室内无线网关 用户手册-6W100-整本手册.pdf`，175092 字节，共 6 页。

首次执行停在 MinerU：`Required dependency not installed`，随后退出码为 1。增加文字型 PDF 的 PyMuPDF 解析路径后，已真实提取各页文字及图片，并保留 OCR 分支。

现有 BGE 模型位于本机缓存的 `models/models/BAAI--bge-m3/snapshots/master`，与 `.env` 中路径不一致。通过验收脚本 `--bge-model-path` 指定现有目录，无需下载。已加载真实模型并生成一条 1024 维稠密向量和含 14 个非零特征的稀疏向量。

Docker 容器启动后，Milvus 与 MinIO 已连接成功。已将 PDF 第一张真实图片上传 MinIO，回读对象大小为 1343 字节，与本地一致。

启用视觉模型时曾返回 `401 Incorrect API key`。用户确认未配置模型 API 后，增加显式 `--no-llm` 模式并继续验收，无需提供 API 密钥。此模式图片照常上传但不生成描述，主体采用文件名；它不表示完成了语义主体识别。BGE 本地向量化继续执行。

最终真实验收通过：

- 文档标识：`b5ce6eb73e2f4466badcd7dfcd467ed1`。
- MinIO 桶 `knowledge-base`：该文档前缀下 23 个真实图片对象。
- Milvus `kb_chunks`：8 条切片，`kb_item_names`：1 条主体记录。
- 稠密向量维度：1024，同时保存稀疏向量。
- 按文档 ID 强一致性回读确认记录数量，按主键回读确认切片文本和向量维度。
- 输出与回读结果：`temp-files/b5ce6eb73e2f4466badcd7dfcd467ed1/acceptance.json`。

本地 `.env` 已修正 `BGE_M3_PATH` 为实际缓存目录，并设置 `IMPORT_USE_LLM=false`；未修改 API 密钥。流程没有使用模拟向量或模拟数据库。

验收脚本：

```powershell
.venv/Scripts/python.exe -m app.tool.verify_import --no-llm
```

脚本逐节点保存 `temp-files/<document_id>/state.json`。修复配置后可用 `--resume <state.json>` 继续，避免重新创建文档标识。全部节点完成后按文档 ID 回读 Milvus 切片数量、主体记录、向量维度与文本，并检查 MinIO 对象；成功时输出 `acceptance.json`。状态文件含文档与向量，应保留在已忽略的运行目录。


日常导入也支持 `--no-llm`，或在 `.env` 中设置 `IMPORT_USE_LLM=false`：

```powershell
.venv/Scripts/python.exe -m app.processor.import_process.agent.kb_import_workflow "path/to/manual.pdf" --no-llm --stream
```

本次新增文字型 PDF 真实解析测试、无 LLM 完整流程测试。离线测试共 15 项通过；它们与上述真实服务验收分别记录。
