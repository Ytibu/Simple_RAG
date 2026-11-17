# 文件导入流程

## 当前范围与节点

所有 PDF 统一使用 MinerU 云 API 解析，包括文字型 PDF 和扫描件。OCR、版面分析、表格及公式处理均由云服务完成。Markdown 输入直接进入图片处理节点，导入结果供查询工作流检索。

统一工作流位于 `app/processor/import_process/agent/kb_import_workflow.py`，旧入口 `main_praph.py` 复用同一张图。

| 节点 | 职责 |
| --- | --- |
| `node_entry` | 校验 PDF/Markdown 文件，生成文档标识，使用调用方的 `state.local_dir` 创建输出目录并设置路由 |
| `node_pdf_to_md` | 上传 PDF 到 MinerU 云 API，轮询、下载并解压结果，将实际 Markdown 路径写入状态 |
| `node_md_img` | 使用文件标题及图片前后文生成视觉描述，限流调用模型，上传图片并替换本地引用 |
| `node_document_split` | 按标题、段落及句子边界切分长内容，合并同父标题短块，保留来源和顺序 |
| `node_item_name_recognition` | 使用前 K 个切片及文件标题识别主体，回填切片并更新 `chunks.json`；禁用 LLM 或识别失败时使用文件标题，也可直接指定主体 |
| `node_bge_embedding` | 将主体名称与正文拼接，本地 BGE-M3 分批生成双向量；同步更新 `chunks`、`embeddings_content`，单独保留主体向量 |
| `node_import_milvus` | 检查或创建集合，upsert 切片和主体向量，加载两个集合后报告成功数量 |

流程沿用七节点顺序。PDF 经过全部节点，Markdown 跳过 PDF 转换节点。主体识别文档中的主体向量生成及存储操作，分别由第六、第七节点统一完成。

切分上限为 `ImportConfig.max_content_length`，短块阈值为 `min_content_length`。短块仅在父标题相同且合并后不超过上限时合并。代码围栏按字符类型和长度配对，围栏内的 `#` 保留为正文。无标题文本使用文件标题。切片 `metadata` 保存 `file_title`、`part`、来源和顺序。

主体识别的空结果、无效 JSON 或模型异常会记录警告并采用文件标题，后续继续向量化和入库。PDF 解析、图片处理、向量生成及存储失败会终止导入，任务状态记录具体失败节点。

## MinerU 云 API 配置

配置定义在 `app/conf/mineru_config.py`，从本地 `.env` 读取。真实 token 不提交 Git。

```dotenv
MINERU_BASE_URL=https://mineru.net/api/v4
MINERU_API_TOKEN=在本地填写真实token
MINERU_MODEL_VERSION=vlm
MINERU_REQUEST_TIMEOUT=60
MINERU_TASK_TIMEOUT=600
MINERU_POLL_INTERVAL=3
```

请求流程：`POST /file-urls/batch` 申请地址 → PUT 上传 PDF → `GET /extract-results/batch/{batch_id}` 轮询 → 下载结果 ZIP → 校验归档路径并解压。协议参考 [MinerU 官方文档](https://mineru.net/doc/docs/index_en/)。

Bearer token 只发送给云 API，不附带到签名上传或下载请求。日志和任务记录不输出 token 或签名 URL。缺少配置、请求失败、任务失败、超时、无有效正文时，节点报错并停止导入。

本地 PDF 解析分支及其切换参数已移除，不再需要安装或配置本地 MinerU OCR 模型。模型下载缓存仍可能供 BGE 使用，与 PDF 云端解析无关。

## 其他运行配置

- LLM：`OPENAI_API_KEY`、`OPENAI_API_BASE`、`ITEM_MODEL`、`VL_MODEL`。`IMPORT_USE_LLM=false` 或 `--no-llm` 只关闭图片描述和主体识别，PDF 仍调用 MinerU 云 API。
- 向量：`BGE_M3_PATH` 指向完整本地模型目录，`BGE_DEVICE` 指定设备；CPU 自动禁用 FP16。`BGE_EMBEDDING_DIM=1024` 与云端嵌入的 `EMBEDDING_DIM` 独立。
- Milvus：`MILVUS_URL`、`CHUNKS_COLLECTION`、`ITEM_NAME_COLLECTION`；两个集合名必须不同。
- MinIO：`MINIO_ENDPOINT`、访问凭据、`MINIO_BUCKET_NAME`；HTTPS 可设 `MINIO_SECURE=true`。
- `MD_ROOT_DIR`：命令行默认输出根目录。命令行在其下生成独立目录，再通过 `state.local_dir` 传入节点。

## 执行与输出目录

使用项目现有 Python 3.11 环境，在仓库根目录执行：

```powershell
.venv/Scripts/python.exe -m app.processor.import_process.agent.kb_import_workflow "path/to/manual.pdf" --stream
.venv/Scripts/python.exe -m app.processor.import_process.agent.kb_import_workflow "path/to/manual.pdf" --output-dir "temp-files/manual-import"
```

直接调用时必须明确传入本次导入目录，入口节点不追加 UUID 子目录：

```python
state = create_default_state(
    local_file_path="resource/doc/manual.pdf",
    local_dir="temp-files/manual-import",
)
result = KBImportWorkflow().run(state)
```

目录不存在时创建，已存在时可使用；后续节点会写入固定名称的结果文件，不同任务应使用不同目录。原输入文件不会被修改。

输出包含：

- `mineru_task.json`：云端批次 ID 和状态。
- `mineru_result.zip`：云端原始解析结果。
- `mineru/full.md` 与图片目录：保留结果中的相对路径，`state.md_path` 指向此 Markdown。
- `processed.md`：图片上传并按需补充描述后的正文。
- `chunks.json`、`embeddings.json`：切片及入库向量记录。

## 存储约定

切片集合使用 INT64 `chunk_id` 主键，以及 `document_id`、`content`、`title`、`parent_title`、`item_name`、`metadata`、`dense_vector`、`sparse_vector`。主体集合保存首个切片主键、文档标识、主体名称和两类向量。稠密索引使用 COSINE，稀疏索引使用 IP。

已有集合不兼容时停止，不删除历史数据。同一向量状态可重试 upsert；重新执行整个导入会创建新文档，不自动去重。跨集合写入没有事务，失败时可能已部分写入。

MinIO 不自动公开桶，Markdown 保存持久的 `s3://bucket/object` 引用。展示时由调用方生成授权链接。图片节点支持普通内联图片和 `<含空格路径>`，不解析 HTML 图片、引用式图片或远程图片内容。当前仅实现普通文档切分。

## 自动化测试与真实验收

不请求外部服务的自动化测试：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py" -v
```

测试覆盖真实图编排、PDF 云 API 调用协议、失败与超时、凭据隔离、归档路径校验、图片上下文、短块合并、代码围栏、主体识别兜底、向量输入及 Milvus schema。模型与存储使用替身的测试用于验证编排和数据传递，真实服务验收使用下方脚本。

完整真实验收会调用 API 并写存储：

```powershell
.venv/Scripts/python.exe -m app.tool.verify_import
```

默认选择 `resource/` 中最小的 PDF，可用 `--file`、`--output-dir` 指定输入及输出。逐节点保存 `state.json`，失败修复后可用 `--resume <state.json>` 继续。成功后回读 Milvus 计数、切片文本、向量维度，并核对全部 MinIO 图片引用，输出 `acceptance.json`。

## 验证历史与处理记录

- 2026-11-17 对照七节点笔记补齐功能后，56 项离线测试通过。真实导入同一份 H3C LA2608 手册，完成 MinerU 云解析、4 张图片的视觉描述与 MinIO 上传、主体识别、本地 BGE-M3 向量化及 Milvus 入库回读。10 个初切块合并为 7 个最终切片，主体为 `H3C LA2608 室内无线网关`，主体集合 1 条记录，向量维度 1024。文档标识为 `33f2649b4a9845ce82d13cd234ea5af7`，报告为 `temp-files/import-node-alignment-20261117/acceptance.json`。首次请求受执行环境网络限制，恢复网络访问后从入口检查点继续完成，切片与向量备份一致性检查通过。
- 早期本地解析曾报 `Required dependency not installed`，旧方案临时增加文字层提取；这些本地解析实现现已移除。
- 初次模型调用出现 `401 Incorrect API key`，后续配置有效密钥和最新 base URL 后调用通过。视觉模型曾返回 `404 Model not exist`，调整为服务支持的 `qwen3-vl-plus` 后通过。
- 修正过 BGE 本地目录层级、BGE 与云嵌入维度混用、路由静态方法签名及配置重构中的导出缺失问题。
- 2026-11-13 的旧方案验收：6 页 H3C LA2608 手册，8 条切片、1 条主体记录、23 个图片对象；属于历史结果，不代表当前云解析输出。
- 2026-11-14 的旧方案 LLM 验收：23 张图片生成描述后形成 13 个切片，主体识别为 `H3C LA2608 室内无线网关`，入库回读通过。历史文档标识为 `19122f4ce64d42c3b0901d76a06fb212`。
- 2026-11-14 的 MinerU 云 API 验收：同一份 6 页 PDF 经上传、排队、运行、下载完成，得到 3818 字符正文和 4 张图片，全部本地图片引用存在。产物位于 `temp-files/mineru-online-dfb382c9c92846fca6e858985634766c/`。

运行产物含原文和向量，保存在已忽略的 `temp-files/`，不纳入 Git。真实转换测试与全链路存储验收应分别记录，不将协议模拟测试视为云服务或模型质量验证。

当前云 API 唯一路径已重新实测通过：未传任何解析模式参数，得到 3818 字符正文及 4 个有效本地图片引用。验证结果位于 `temp-files/mineru-cloud-24f23fbf3e4d476daa712df5fe49751a/conversion_verification.json`。本次仅重新验收 PDF 转换，没有重复执行 LLM、BGE 或数据库写入。
