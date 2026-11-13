# 文件导入流程

## 范围与节点

本次实现文件导入，不实现 `app/processor/query_process/` 中的检索流程。
统一入口是 `app/processor/import_process/agent/kb_import_workflow.py`，旧入口 `main_praph.py` 复用同一张图。

| 节点 | 输入与输出 |
| --- | --- |
| `node_entry` | 校验存在的 PDF、MD、Markdown 文件，生成导入标识和独立输出目录，设置路由 |
| `node_pdf_to_md` | 使用本机 magic-pdf 0.6 的 UNIPipe 解析 PDF，保存 Markdown 和图片；Markdown 输入跳过此节点 |
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
- PDF 使用本地 MinerU 模型及其 `magic-pdf.json` 配置，不调用 `MINERU_BASE_URL` 云端接口。相关模型须提前准备。

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

尚未执行真实 PDF 模型推理、付费 LLM/视觉请求、真实 BGE 编码、MinIO 上传或 Milvus 写入。离线测试通过仅说明流程和接口约定已验证，不能视为外部服务联调通过。
