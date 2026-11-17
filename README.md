# 知识库项目

基于 FastAPI、LangGraph 和 Milvus 的文档知识库，支持 PDF、Markdown 导入、混合向量检索、带原文引用的回答及连续对话。

PDF 使用 MinerU 云 API 完成 OCR 和版面解析。切片及主体名称由本地 BGE-M3 向量化，图片保存到 MinIO，查询历史保存到 MongoDB。查询通过普通检索、HyDE、RRF 融合及重排生成回答。

## 项目能力与服务分工

| 能力 | 实现方式 |
| --- | --- |
| 文件导入 | 支持 `.pdf`、`.md`、`.markdown`，提供页面、HTTP 接口和 CLI |
| PDF 深度解析 | MinerU 云 API 处理 OCR、版面、表格和公式，下载 Markdown 与图片产物 |
| 图片处理 | 本地图片上传 MinIO，可调用视觉模型生成描述 |
| 文档组织 | 按标题和长度切片，保留文档、主体、标题及顺序信息 |
| 向量存储 | 本地 BGE-M3 生成 1024 维稠密向量及稀疏向量，写入 Milvus |
| 知识库查询 | 主体确认、混合检索、HyDE、RRF 融合、重排、带引用的回答 |
| 连续对话 | MongoDB 保存整轮问题和答案，后续提问结合历史改写 |
| 调试验收 | 节点日志、中间产物、导入检查点、存储回读及接口验证 |

FastAPI 同时提供页面与接口。浏览器提交导入后，由后台任务执行导入图；查询请求由查询服务执行查询图，等待回答完成后返回结果。两个工作流共享已配置的模型与存储服务。

## 环境要求

- Python `>=3.11,<3.12`，仓库指定版本为 `3.11.16`。
- uv，用于同步依赖和运行项目。
- Docker Desktop 与 Docker Compose，用于启动 Milvus、MinIO、MongoDB。
- 可用的 MinerU 云 API token，以及支持文本和视觉模型的 OpenAI 兼容服务。
- 已下载的 BGE-M3 模型。本地重排使用 bge-reranker-large，也可配置 LLM 重排。

以下命令在仓库根目录执行，示例采用 Windows PowerShell。

## 日常启动

已经完成环境准备和 `.env` 配置时，先打开 Docker Desktop，再在根目录执行以下命令。

```powershell
docker compose --project-directory . -f docs/compose.infra.yml up -d
docker compose --project-directory . -f docs/compose.infra.yml ps
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/import` 导入文件，完成后在 `http://127.0.0.1:8000/query` 提问。接口调试入口为 `http://127.0.0.1:8000/docs`。

`docs/compose.infra.yml` 是启动命令使用的配置文件，下面各节已包含运行项目所需的准备和操作说明。

## 首次准备

### 1. 检查工具并同步依赖

```powershell
uv --version
docker version
docker compose version
uv sync --locked
uv run python --version
```

`uv sync --locked` 按 `uv.lock` 创建或同步 `.venv`，首次执行可能下载 Python 和依赖。已有环境可直接使用 `.venv/Scripts/python.exe`。

### 2. 配置环境变量

首次使用时从模板创建 `.env`，然后填写服务地址、密钥和本地模型路径。

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

| 配置项 | 用途与填写要求 |
| --- | --- |
| `OPENAI_API_BASE` | 服务商提供的 OpenAI 兼容接口地址，填写完整 URL |
| `OPENAI_API_KEY` | 对应接口的真实 API key |
| `LLM_DEFAULT_MODEL` | 查询改写、主体确认和回答模型，例如服务已开通的 `qwen-flash` |
| `ITEM_MODEL` | 导入时识别文档主体的文本模型 |
| `VL_MODEL` | 导入时生成图片描述的视觉模型，例如服务已开通的 `qwen3-vl-plus` |
| `IMPORT_USE_LLM` | 通常设为 `true`，启用图片描述与主体识别 |
| `MINERU_BASE_URL` | MinerU 云 API 地址，通常为 `https://mineru.net/api/v4` |
| `MINERU_API_TOKEN` | MinerU 云服务 token |
| `MINERU_MODEL_VERSION` | 云端解析模型版本，默认 `vlm` |
| `BGE_M3_PATH` | BGE-M3 实际模型目录，目录中应直接包含配置、权重与 tokenizer 文件 |
| `BGE_EMBEDDING_DIM` | BGE-M3 向量维度，设为 `1024` |
| `BGE_RERANKER_LARGE` | bge-reranker-large 实际模型目录 |
| `QUERY_RERANKER` | `auto` 优先使用已有本地重排模型，`local` 强制本地，`llm` 使用文本模型打分 |
| `MD_ROOT_DIR` | 上传文件和导入产物的根目录，例如 `./temp-files/` |

缓存下载的模型可能位于 `models/BAAI--模型名/snapshots/master`，应填写实际包含模型文件的那一层。模型下载脚本位于 `app/tool/download_bgem3.py` 和 `app/tool/download_reranker.py`，使用前按本机目录修改脚本中的缓存路径。

本机 CPU 运行可使用以下配置。

```dotenv
BGE_DEVICE=cpu
BGE_FP16=False
BGE_RERANKER_DEVICE=cpu
BGE_RERANKER_FP16=0
BGE_EMBEDDING_DIM=1024
QUERY_RERANKER=auto
```

当前代码通过 PyTorch 执行本地模型，CPU 首次加载和重排需要一定时间。Intel 核显加速及 ONNX 推理属于后续可评估的优化方案。

编辑 `OPENAI_API_BASE` 时，将说明文字放在独立注释行，确保配置值为纯 URL。模型缓存相关变量用于本地嵌入和重排模型，PDF 解析统一调用 MinerU 云 API。修改 `.env` 后重启应用以重新加载配置。

### 环境配置示例

下面的配置展示本机 CPU 运行所需的主要字段。将密钥、API 地址和模型目录替换为实际值，保存到根目录 `.env`。

```dotenv
# 文本与视觉模型服务
OPENAI_API_BASE=https://your-provider.example/compatible-mode/v1
OPENAI_API_KEY=replace-with-your-key
LLM_DEFAULT_MODEL=qwen-flash
LLM_DEFAULT_TEMPERATURE=0.1
ITEM_MODEL=qwen-flash
VL_MODEL=qwen3-vl-plus
IMPORT_USE_LLM=true

# PDF 云端解析
MINERU_BASE_URL=https://mineru.net/api/v4
MINERU_API_TOKEN=replace-with-your-token
MINERU_MODEL_VERSION=vlm
MINERU_REQUEST_TIMEOUT=60
MINERU_TASK_TIMEOUT=600
MINERU_POLL_INTERVAL=3

# 本地模型，指向实际包含权重和 tokenizer 的目录
BGE_M3_PATH=E:/ai_models/bge-m3
BGE_DEVICE=cpu
BGE_FP16=False
BGE_EMBEDDING_DIM=1024
BGE_RERANKER_LARGE=E:/ai_models/bge-reranker-large
BGE_RERANKER_DEVICE=cpu
BGE_RERANKER_FP16=0
QUERY_RERANKER=auto

# Milvus
MILVUS_URL=http://127.0.0.1:19530
CHUNKS_COLLECTION=kb_chunks
ITEM_NAME_COLLECTION=kb_item_names

# MongoDB
MONGO_URL=mongodb://127.0.0.1:27017
MONGO_DB_NAME=kb001

# MinIO
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_NAME=knowledge-base
MINIO_SECURE=false

# 本地产物目录
MD_ROOT_DIR=./temp-files/
```

`MINERU_REQUEST_TIMEOUT` 控制单次 HTTP 请求超时，`MINERU_TASK_TIMEOUT` 控制云端任务等待总时间，`MINERU_POLL_INTERVAL` 控制轮询间隔，单位均为秒。扫描件或长文档可根据实际处理时间调整等待设置。

`QUERY_RERANKER=auto` 在本地目录存在时使用本地模型，缺少目录时调用 LLM 重排。`local` 要求本地模型可用，`llm` 直接使用 `LLM_DEFAULT_MODEL` 打分。选择 LLM 重排时，本地 BGE-M3 仍用于查询向量化。

### 3. 启动存储服务

先启动 Docker Desktop，再执行以下命令。

```powershell
docker compose --project-directory . -f docs/compose.infra.yml config --quiet
docker compose --project-directory . -f docs/compose.infra.yml up -d
docker compose --project-directory . -f docs/compose.infra.yml ps
```

首次启动可能下载镜像。Compose 同时启动 Milvus 所需的 etcd 和内部对象存储。容器显示运行后，可结合日志确认服务完成初始化。

```powershell
docker compose --project-directory . -f docs/compose.infra.yml logs --tail 100 milvus mongo minio
```

与当前 Compose 对应的本地连接配置如下。

```dotenv
MILVUS_URL=http://127.0.0.1:19530
CHUNKS_COLLECTION=kb_chunks
ITEM_NAME_COLLECTION=kb_item_names
MONGO_URL=mongodb://127.0.0.1:27017
MONGO_DB_NAME=kb001
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_NAME=knowledge-base
MINIO_SECURE=false
```

Milvus 集合与图片桶在导入时按需创建。上述命令通过 `--project-directory .` 将数据目录固定在仓库根目录，数据持久化在 `volumes/` 下。默认凭据用于本地开发，真实密钥保存在本地 `.env`。

| 容器 | 用途 | 主机端口 |
| --- | --- | --- |
| `kb-minio` | 文档图片对象存储与控制台 | `9000`、`9001` |
| `kb-milvus-standalone` | 文档与主体向量存储 | `19530`、`9091` |
| `kb-milvus-etcd` | Milvus 元数据依赖 | 容器内部使用 |
| `kb-milvus-minio` | Milvus 内部对象存储 | 容器内部使用 |
| `kb-mongo` | 查询会话历史 | `27017` |
| `kb-attu` | Milvus 可视化管理 | `7000` |

业务图片使用 `kb-minio`，Milvus 自身的数据使用 `kb-milvus-minio`。应用配置中的 `MINIO_ENDPOINT` 对应业务图片存储的主机端口。

## 启动应用

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

保持终端运行，通过以下地址使用项目。

| 地址 | 功能 |
| --- | --- |
| `http://127.0.0.1:8000/import` | 上传 PDF 或 Markdown，查看导入进度 |
| `http://127.0.0.1:8000/` 或 `/query` | 智能咨询工作台，查询已导入文档、查看引用原文并连续提问 |
| `http://127.0.0.1:8000/docs` | 交互式 API 文档 |
| `http://127.0.0.1:9001` | MinIO 控制台，默认账号和密码均为 `minioadmin` |
| `http://127.0.0.1:7000` | Attu，查看 Milvus 数据 |

开发时可在启动命令后添加 `--reload`。导入任务进度保存在应用进程中，使用单个 worker，运行任务期间应保持进程持续运行。按 `Ctrl+C` 停止应用，保留数据并停止基础设施可执行以下命令。

```powershell
docker compose --project-directory . -f docs/compose.infra.yml stop
```

日常启动顺序为 Docker Desktop、Compose 基础设施、Uvicorn 应用。已有 `.env` 和 `.venv` 时，可直接执行对应启动命令。

## 导入文件与查询

先通过 `/import` 上传文档，等待任务完成并显示切片数量，再在 `/query` 提问。Markdown 中的本地图片路径按 Markdown 所在目录解析，带本地图片的文档可通过 CLI 保持原有目录关系。

也可以直接运行命令行入口。

```powershell
uv run python -m app.processor.import_process.agent.kb_import_workflow "resource/doc/manual.pdf" --stream
uv run python -m app.processor.query_process.agent.kb_query_workflow "H3C LA2608支持哪些厂商的无线控制器？"
```

将 `resource/doc/manual.pdf` 替换为实际文件路径。导入参数 `--output-dir` 指定本次输出目录，默认在 `MD_ROOT_DIR` 下创建独立任务目录。`--no-llm` 关闭导入时的图片描述和主体识别，PDF 仍调用 MinerU 云 API。查询参数 `--top-k 3` 设置返回切片数，`--no-hyde` 关闭假设性文档召回。

### HTTP 接口

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/imports` | multipart 上传，文件字段为 `files`，返回任务 ID |
| `GET /api/imports/{task_id}` | 获取导入进度、状态和切片数量 |
| `POST /api/queries` | 提交问题，返回回答、引用与会话 ID |
| `POST /api/queries/submit` | 提交后台查询，立即返回任务 ID 与 SSE 地址 |
| `GET /api/queries/tasks/{task_id}/events` | 实时接收节点进度、答案增量和完整结果 |
| `GET /api/queries/{session_id}/history` | 获取会话历史 |
| `DELETE /api/queries/{session_id}/history` | 清空指定会话历史 |
| `GET /api/query-images?uri=s3%3A...` | 获取引用图片的临时授权地址 |
| `GET /health` | 检查服务是否存活 |

PowerShell 查询示例可直接调用已启动的服务。

```powershell
$queryBody = @{
    question = "H3C LA2608支持哪些厂商的无线控制器？"
    top_k = 5
    use_hyde = $true
    use_web = $false
} | ConvertTo-Json

$queryResult = Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/api/queries" `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($queryBody))

$queryResult.answer
$queryResult.sources
```

连续提问时，在下一次请求中加入上一次返回的 `session_id`。默认查询使用知识库内容，`use_web=true` 才调用配置的 MCP 联网搜索。

### 上传与进度查询

在 PowerShell 中使用 `curl.exe` 发送 multipart 文件。将文件路径替换为本地实际文件，返回的 `task_ids` 按上传顺序排列。

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/imports" -F "files=@resource/doc/manual.pdf"
```

成功接收文件后返回 HTTP 202，响应格式如下，示例任务 ID 需要替换为真实返回值。

```json
{
  "code": 200,
  "message": "已接收 1 个文件",
  "task_ids": ["example_task_id"]
}
```

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/imports/example_task_id"
```

导入状态依次为 `pending`、`processing`、`completed`，发生错误时为 `failed`。进度响应包含 `done_list`、`running_list`，完成后提供 `imported_count`。收到上传响应后，可持续查询进度，直至任务完成。

Web 上传支持同时选择多个文件。带本地图片的 Markdown 通过 CLI 导入时，应保持 Markdown 和图片原有的目录关系。

### 查询参数与返回结果

| 请求字段 | 默认值 | 说明 |
| --- | --- | --- |
| `question` | 必填 | 长度为 1–4000，空白问题返回 422 |
| `session_id` | 自动生成 | 继续同一会话时使用上次返回值，最多 128 字符，支持字母、数字、下划线和连字符 |
| `top_k` | `5` | 引用数量上限，HTTP 接口范围 1–12，动态重排最多保留 10 条 |
| `use_hyde` | `true` | 额外生成假设性手册文本用于召回 |
| `use_web` | `false` | 显式启用 MCP 联网搜索 |

查询响应的 `answer` 为最终回答，`sources` 为送入回答模型的原文切片。每个来源包含 `citation`、`chunk_id`、`document_id`、`item_name`、`title`、`parent_title`、`metadata`、`content` 和 `rerank_score`。回答中的 `[1]` 对应 `citation=1` 的来源。

响应同时提供 `rewritten_query` 便于核对连续提问的改写结果，`item_names` 表示确认的主体，`item_candidates` 表示可选主体，`reranker_used` 表示本轮实际使用的重排方式。需要用户明确主体或知识库缺少相关资料时，接口会返回相应提示。

以下示例继续上一次 PowerShell 查询，并读取会话历史。

```powershell
$followupBody = @{
    question = "它在配置前需要满足哪些要求？"
    session_id = $queryResult.session_id
} | ConvertTo-Json

$followupResult = Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/api/queries" `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($followupBody))

$followupResult.answer
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/queries/$($queryResult.session_id)/history"
```

每轮查询从最近 5 轮中的成功对话获取改写上下文，历史接口按时间顺序返回最近 20 轮，可用 `limit` 调整到 1–100 轮。MongoDB 的 `query_turns` 在开始处理时保存问题并标记 `processing`，随后更新为 `completed` 或 `failed`，保存改写问题、产品、引用及图片。确认产品后，相关澄清记录也会补齐产品名称。

查询页默认流式输出，提供处理进度、候选产品按钮、引用原文和图片，也可关闭流式输出。Enter 发送，Shift+Enter 换行。新建会话使用新的上下文，“清空历史”删除当前会话记录。资料导入页支持拖拽及多文件选择，上传进度条显示传输进度，上传后轮询各节点执行状态。

### 流式查询接口

提交任务后，使用返回的 `stream_url` 接收 SSE。在仓库根目录的 PowerShell 中执行以下示例。

```powershell
$body = @{question = "H3C LA2608 支持哪些无线控制器？"; use_hyde = $true; use_web = $false} | ConvertTo-Json
$task = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/queries/submit" `
    -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
curl.exe -N "http://127.0.0.1:8000$($task.stream_url)"
```

事件依次包含 `ready` 握手、`progress` 节点进度、`delta` 模型增量和 `final` 完整答案，故障发送 `error`。`progress` 提供 `done_list` 与 `running_list`，`delta` 提供 `text`，`final` 提供答案、引用和 `image_urls`。每 10 秒发送空闲心跳，客户端可用 `Last-Event-ID` 恢复事件。前端使用 EventSource 自动重连，并在刷新后尝试恢复当前任务。

兼容笔记中的 `POST /query`、`GET /stream/{session_id}`、`GET/DELETE /history/{session_id}` 和 `/chat.html`。请求可使用 `query` 字段，`is_stream=true` 时立即返回后台任务信息。同一会话已有任务时返回 409，应等待当前轮完成。

任务事件保存在单进程内存，完成任务保留约 15 分钟，后续提交触发清理，总任务上限 128。使用单个 Uvicorn worker。服务重启后可读取 MongoDB 已保存历史，内存中的 SSE 任务需要重新提交。

## 工作流与数据组织

### 文件导入的七个节点

| 节点 | 输入与操作 | 主要输出 |
| --- | --- | --- |
| `node_entry` | 校验文件，生成文档标识，读取 `state.local_dir` | 文档 ID、输出路径、文件类型路由 |
| `node_pdf_to_md` | 申请 MinerU 上传地址、上传文件、轮询、下载并解压结果 | Markdown 路径及正文 |
| `node_md_img` | 读取本地图片，上传 MinIO，按配置生成视觉描述 | 更新图片引用的正文 |
| `node_document_split` | 按标题、段落和句子切分，合并同父标题短块并控制长度 | 切片列表及来源元数据 |
| `node_item_name_recognition` | 结合文件标题和前 K 个切片识别主体，失败时采用文件标题 | 主体名称及切片回填 |
| `node_bge_embedding` | 将主体名称与切片正文拼接生成 dense、sparse 向量，另行生成主体向量 | 带双向量的切片及主体向量 |
| `node_import_milvus` | 检查集合 schema，写入切片与主体向量并加载集合 | 成功写入的切片数量 |

Markdown 从入口直接进入图片处理节点。PDF 上传到 MinerU 云端后，解析结果中的图片和 Markdown 保持相对目录关系。

直接使用 Python 调用导入图时，调用方需要明确传入本次输出目录。

```python
from app.processor.import_process import create_default_state
from app.processor.import_process.agent.kb_import_workflow import KBImportWorkflow

state = create_default_state(
    local_file_path="resource/doc/manual.pdf",
    local_dir="temp-files/manual-import",
)
result = KBImportWorkflow().run(state)
print(result["document_id"], result["imported_count"])
```

`state.local_dir` 为本次任务实际写入目录。不同任务应分配不同目录，已存在目录中的同名产物可能被覆盖。通过 Web 或 CLI 启动时，入口会准备独立任务目录。

| 本地产物 | 内容 |
| --- | --- |
| `source/` | Web 上传文件的暂存目录 |
| `mineru_task.json` | PDF 云端批次标识及任务状态 |
| `mineru_result.zip` | 云端解析结果归档 |
| `mineru/full.md` 及图片目录 | 解压后的 PDF 正文和图片 |
| `processed.md` | 上传图片并按配置补充描述后的 Markdown |
| `chunks.json` | 切片文本与元数据 |
| `embeddings.json` | 准备写入 Milvus 的向量记录 |
| `state.json`、`acceptance.json` | 导入验收脚本保存的检查点与回读报告 |

图片节点使用持久的 `s3://bucket/object` 引用保存对象位置。查询返回的切片可能包含此类引用，图片展示需要调用方生成授权访问地址。

### 查询的七个节点

| 节点 | 职责 |
| --- | --- |
| `node_item_name_confirm` | 结合历史提取产品名并改写问题，按匹配分自动确认或返回候选，支持用户确认后恢复原问题 |
| `node_search_embedding` | 在主体范围内执行 BGE-M3 稠密、稀疏混合检索 |
| `node_search_embedding_hyde` | 生成假设性手册文本，执行第二路混合检索 |
| `node_web_search_mcp` | 请求启用时调用配置的 MCP 搜索服务 |
| `node_rrf` | 等待召回分支完成，按文档和主键去重，融合排名 |
| `node_rerank` | 合并本地与网页候选打分，根据分数断崖动态保留 Top-K |
| `node_answer_output` | 根据入选原文生成答案和引用编号 |

普通检索和 HyDE 每路默认召回 12 条，融合后最多保留 12 条，再由重排节点选取默认 5 条。回答上下文中的原文总长度上限为 18000 字符。HyDE 文本用于召回，最终回答依据已检索到的原文生成。

### 存储与重试

Milvus 的 `kb_chunks` 保存切片，`kb_item_names` 保存主体名称及向量，两者使用 INT64 类型的 `chunk_id` 主键。切片字段还包括文档 ID、主体、标题、正文、元数据以及 dense、sparse 两类向量。稠密索引使用 COSINE，稀疏索引使用 IP。

导入会检查已有集合的字段类型和向量维度。已有 schema 不兼容时，需要调整为兼容集合或执行数据迁移后继续。修改嵌入模型时应同步核对已有向量的维度及语义空间。

同一状态重试写入节点使用 upsert。重新运行完整导入会生成新的文档 ID，因此同一源文件重复导入可能产生多份记录。切片集合与主体集合之间的写入缺少跨集合事务，失败后应结合检查点和文档 ID 检查实际写入情况。

原文件默认暂存到本地。需要原文件归档时，可配置 `MINIO_IMPORT_ARCHIVE_BUCKET` 为预先创建的私有桶；图片存储仍使用 `MINIO_BUCKET_NAME`。

### 可选联网搜索

启用 `use_web=true` 或 CLI `--web` 时，配置 `MCP_DASHSCOPE_BASE_URL` 指向 MCP 搜索服务。适配器使用 `OPENAI_API_KEY` 鉴权，支持 Streamable HTTP 及旧式 HTTP+SSE，依次执行初始化、工具发现和搜索调用。优先选择 `bailian_web_search`，支持参数 `query`、`search_query` 或 `q`，工具允许时传入 `count=5`。返回的 `pages` 解析为独立网页，保留标题、链接和非空摘要。

百炼可配置 `MCP_DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp`。当前本机旧 `/sse` 地址返回空流，适配器在此情形下尝试同服务的 `/mcp` 地址，2026-11-17 实测得到 5 条带链接的网页摘要。

普通检索和 HyDE 使用稠密/稀疏得分 0.8/0.2 加权，确认产品的分数 >0.85 时自动继续，0.6–0.85 时提示选择候选。两路本地召回通过 RRF 融合，再与网页结果统一精排。动态截断默认至少保留 3 条，最多受 `top_k` 和 10 条硬上限约束。相邻分数下降达到绝对差 0.5 或相对差 25% 时截断。相关参数集中在 `QueryConfig`。

## 测试与故障排查

自动化测试使用标准库 unittest。

```powershell
uv run python -m unittest discover -s tests -p "test_*.py" -v
```

真实联调使用以下命令，会调用模型和存储服务。导入验收会写入文档数据，查询验收会保存会话记录。

```powershell
uv run python -m app.tool.verify_import --file "resource/doc/manual.pdf"
uv run python -m app.tool.verify_query
```

查询验收默认使用已导入的 LA2608 文档。可通过 `--question`、`--followup` 指定其他文档的问题，通过 `--output` 指定报告路径。本地重排模型必须配置可用，脚本会检查实际使用的重排方式及引用回读结果。

导入验收会逐节点保存 `state.json`，并回读 Milvus 的切片、主体向量及 MinIO 图片对象。发生可恢复错误时，修复配置后可使用保存的检查点继续执行后续节点。

```powershell
uv run python -m app.tool.verify_import --resume "temp-files/example-task/state.json"
```

查询验收通过 FastAPI TestClient 调用真实路由，连续提问两轮，检查本地重排、MongoDB 历史以及引用切片的 Milvus 回读。默认报告路径为 `temp-files/query-verification.json`。该脚本可以直接运行，独立于 Uvicorn 监听端口。

若要验证已启动服务的真实 HTTP 流式传输，可执行以下命令。`--confirm-product` 指定遇到澄清时选择的实际候选，报告同时记录澄清轮次。验收会访问真实模型、MCP 和数据库，并检查引用图片能否通过 HTTP 读取。

```powershell
uv run python -m app.tool.verify_query --stream --web --base-url http://127.0.0.1:8000 --confirm-product "H3C LA2608 室内无线网关" --output temp-files/query-stream-verification.json
```

当前已完成的联调覆盖 LA2608 文档的导入和连续查询。查询能返回“通过 3G/4G 网络连接，仅支持 H3C 无线控制器”，并引用手册原文；手册缺少恢复出厂设置说明时，回答明确提示资料不足。2026-11-17 对照七节点笔记补齐功能后，56 项离线测试通过，并重新完成一次真实云解析、视觉描述、主体识别、本地向量化和存储回读，生成 7 条切片、1 条主体记录、4 个图片对象，向量维度为 1024。报告保存在 `temp-files/import-node-alignment-20261117/acceptance.json`，后续修改可执行上面的测试命令重新验证。

同日补齐 Web 与查询功能后，68 项离线测试通过。真实 HTTP 流程完成产品澄清、候选确认和两轮连续回答，每轮均收到 14 条节点进度事件，答案增量分别为 16、38 条。两轮各返回 5 个来源，MongoDB 回读 3 轮，Milvus 原文及 7 个图片访问检查通过。首个 SSE 事件约 15–32 毫秒，本机 CPU 下两轮处理分别约 78、74 秒。报告为 `temp-files/query-document-alignment-20261117.json`，联网搜索另有 5 条真实网页结果记录于 `temp-files/web-search-alignment-20261117.json`。前端完成脚本语法和事件模拟测试，浏览器视觉效果未重新验收。

| 现象 | 检查方法 |
| --- | --- |
| 页面正常但导入、查询失败 | 查看 `logs/app_YYYYMMDD.log`，结合任务 ID 定位异常节点 |
| MinerU 认证或解析失败 | 核对 `MINERU_BASE_URL`、token、网络与云端任务状态 |
| LLM 返回 401 或模型不存在 | 核对 API key、base URL 与该服务实际开放的模型名 |
| 模型路径错误或尝试下载 | 检查模型目录中的 `config.json`、tokenizer 和权重文件 |
| 连接数据库失败 | 检查 `docker compose ... ps`、容器日志与 `.env` 中的主机端口 |
| 查询耗时较长 | 检查模型加载与重排节点日志，CPU 重排长切片可能超过一分钟 |
| 端口被占用 | 将 Uvicorn 的 `--port` 改为其他端口，并使用对应 URL |

## 项目目录

```text
app/
  main.py                 FastAPI 入口
  api/                    导入、查询和历史接口
  services/               任务执行与会话管理
  processor/              LangGraph 导入图与查询图
  clients/                Milvus、MinIO、MongoDB、MinerU 等客户端
  lm/                     LLM、嵌入及重排调用
  conf/                   环境配置
  web/                    页面和静态资源
docs/                     流程说明与 Docker Compose
tests/                    自动化测试
resource/                 本地测试文档
temp-files/               上传文件、中间结果和验收报告
logs/                     应用日志
volumes/                  Docker 持久化数据
README.md                 项目总览、配置、启动与使用说明
```

`app/main.py` 注册页面、静态资源以及导入和查询 API；`app/services/` 组织任务执行与历史读写；`app/processor/` 实现工作流节点；`app/clients/` 和 `app/lm/` 对接存储、解析及模型服务。
