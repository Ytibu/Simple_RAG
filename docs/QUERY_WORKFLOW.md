# 知识库查询流程

## 功能与调用关系

入口为 `app/processor/query_process/agent/kb_query_workflow.py`，原 V2 入口复用同一实现。查询只读取 Milvus，不创建集合、不改动已导入数据。

| 节点 | 职责 |
| --- | --- |
| `node_item_name_confirm` | 结合最近会话提取产品名和独立问题，逐个查询主体集合。匹配分 >0.85 自动确认，0.6–0.85 返回候选，低于 0.6 丢弃；用户明确选择历史候选后恢复原问题 |
| `node_search_embedding` | BGE-M3 查询编码，在指定主体范围内进行 dense COSINE + sparse IP 混合检索 |
| `node_search_embedding_hyde` | 拼接改写问题和假设性手册内容，执行第二路混合检索；最终答案使用检索出的实际资料 |
| `node_web_search_mcp` | 显式启用时调用 MCP 搜索，解析网页标题、链接及摘要 |
| `node_rrf` | 等待三路召回完成，对普通检索和 HyDE 进行加权 RRF，按文档 ID 和切片主键去重 |
| `node_rerank` | 合并本地融合结果与网页文档，统一打分，按分数断崖动态截断 |
| `node_answer_output` | 结合问题、历史和实际资料生成带引用的答案，流式推送模型增量，并从来源提取图片 |

普通召回与 HyDE 各取 12 个候选，稠密/稀疏归一化得分权重为 0.8/0.2。RRF 默认两路等权、平滑常数 60，融合后最多保留 12 个本地候选，再与网页结果一起重排。`top_k` 是最终数量上限，实际最多 10 个。动态截断默认至少保留 3 个，受请求上限和可用数量约束，相邻分数绝对差达到 0.5 或相对下降达到 25% 时截断。参数位于 `QueryConfig`。

## 配置与本机重排模型

复用已有的 `OPENAI_API_BASE`、`OPENAI_API_KEY`、`LLM_DEFAULT_MODEL`、`BGE_M3_PATH`、`MILVUS_URL`、`CHUNKS_COLLECTION`、`ITEM_NAME_COLLECTION`，不需要重新导入文件。

```dotenv
QUERY_RERANKER=auto
BGE_RERANKER_LARGE=/path/to/bge-reranker-large/snapshots/master
BGE_RERANKER_DEVICE=cpu
BGE_RERANKER_FP16=0
MONGO_URL=mongodb://127.0.0.1:27017
MONGO_DB_NAME=kb001
```

- `auto`：本地目录存在时使用本地 BGE 重排，否则用已配置 LLM 打分；不会自动下载重排模型。
- `local`：强制本地模型，目录缺失时报错；`llm`：强制 LLM 重排。
- 本地模型目录应直接包含 `config.json`、权重和 tokenizer 文件，不能只填写缓存上层目录。
- 无 CUDA 时自动使用 CPU 并禁用 FP16。模型采用懒加载、进程内复用。
- MongoDB 的 `query_turns` 集合每条保存一轮问题、回答、改写问题、候选产品、引用和图片。处理前写入 `processing`，完成后更新为 `completed`，失败更新为 `failed`。查询读取最近 5 轮中的成功轮次作为上下文，确认产品后回填相关澄清轮次。

当前本机验证使用已有的 BGE-M3 与 bge-reranker-large，均为 CPU，无新增模型下载。长切片本地重排约需一分钟以上；首次查询还包含模型加载耗时。查询服务按请求串行运行，适合当前本机单用户调试。

## Web 与接口

启动 `uv run uvicorn app.main:app`，访问 `http://127.0.0.1:8000/query` 或 `/chat.html`。页面提供服务状态、流式开关、HyDE 和联网开关、连续提问、候选产品按钮、处理进度、引用原文、图片、新建会话和清空历史。Enter 发送，Shift+Enter 换行，输入法选词期间的 Enter 保留给输入法。

| 接口 | 用途 |
| --- | --- |
| `POST /api/queries` | 查询并保存成功结果 |
| `POST /api/queries/submit` | 立即接受后台任务，返回会话 ID、任务 ID 和 `stream_url` |
| `GET /api/queries/tasks/{task_id}/events` | SSE 进度、模型增量、完整答案及错误 |
| `GET /api/queries/{session_id}/history` | 按时间顺序返回最近 20 轮 |
| `DELETE /api/queries/{session_id}/history` | 删除指定会话历史，不删除知识库 |
| `GET /api/query-images?uri=s3%3A...` | 为已配置图片桶中的对象生成临时授权跳转 |
| `GET /health` | 服务存活检查 |

兼容笔记中的 `POST /query`、`GET /stream/{session_id}` 和 `GET/DELETE /history/{session_id}`。请求支持 `query` 作为 `question` 的别名。`is_stream=true` 提交后台任务，默认同步返回。当前前端使用按任务隔离的 `stream_url`。

SSE 按 `ready`、`progress`、`delta`、`final` 推送。进度含 `done_list` 和 `running_list`，模型增量位于 `text`，最终结果含答案、引用和 `image_urls`。出错发送 `error` 并结束连接，空闲时每 10 秒发送心跳。`Last-Event-ID` 支持重放未接收事件，刷新页面会尝试恢复当前任务。最终事件在 MongoDB 保存完成后发送。

同一会话有未完成任务时再次提交返回 409。后台任务和事件保存在单进程内存，完成任务在后续提交时按 15 分钟保留期清理，总量上限 128。使用单个 Uvicorn worker，服务重启后从 MongoDB 读取已有历史。完整图片对象地址持久保存，访问时重新生成 10 分钟授权。

请求示例：

```json
{
  "question": "H3C LA2608支持哪些厂商的无线控制器？",
  "session_id": null,
  "top_k": 5,
  "use_hyde": true,
  "use_web": false
}
```

响应包含 `session_id`、`task_id`、`answer`、`rewritten_query`、`item_names`、`item_candidates`、`sources`、`image_urls` 和 `reranker_used`。继续提问时传回相同的 `session_id`。来源保留引用编号、切片主键、文档 ID、标题、元数据与实际送入模型的原文，网页来源还包含 `url`。

`question` 长度为 1–4000，`top_k` 为 1–12；会话 ID 仅允许字母、数字、下划线和连字符。无效输入返回 422，模型或存储服务错误返回 503。接口响应不包含密钥或底层异常详情。

## CLI 与验证

```powershell
uv run python -m app.processor.query_process.agent.kb_query_workflow "H3C LA2608支持哪些无线控制器？"
uv run python -m app.processor.query_process.agent.kb_query_workflow "LA2608配置要求是什么？" --top-k 3 --no-hyde
uv run python -m unittest discover -s tests -p "test_*.py" -q
uv run python -m app.tool.verify_query
```

CLI 的 `--stream` 输出节点完成状态，非逐字输出。真实验收脚本经 FastAPI 接口连续提问两轮，要求实际使用本地重排，并核对 MongoDB 历史以及引用切片的 Milvus 回读；报告保存到 `temp-files/query-verification.json`。仅运行单元测试不会调用模型 API 或向数据库写入。

自动化测试覆盖查询图汇合、RRF 去重、产品澄清、历史改写、来源约束、动态重排、任务事件隔离与重放、模型增量、图片授权、HTTP 参数、历史接口，以及 MCP 两种传输的握手和网页解析。

首轮真实验收查询“恢复出厂设置”及其注意事项，两轮均返回 5 个原文切片，MongoDB 回读到 2 轮，切片内容与 Milvus 完全一致；手册缺少该操作说明，模型明确回答资料不足。记录见 `temp-files/query-verification.json`。

第二组真实验收查询连接网络和支持厂商，再追问配置前提：回答明确给出“通过 3G/4G 网络连接，仅支持 H3C 无线控制器”，引用第 4 页原文；后续问题中的“它”正确改写为 LA2608。两轮均使用本地重排，返回各 5 个切片，MongoDB 和 Milvus 回读均通过。记录见 `temp-files/query-positive-verification.json`。上述两组完整查询经 FastAPI TestClient 执行了真实路由、模型与存储调用；另对运行中的 Uvicorn 发送真实 HTTP 请求，确认历史接口返回 200、2 轮记录，空问题返回 422，OpenAPI 返回 200。浏览器操作按用户要求停止，后续均为接口测试。

## 可选联网搜索边界

`use_web=true` 或 CLI `--web` 调用 `MCP_DASHSCOPE_BASE_URL` 与 `OPENAI_API_KEY`。支持旧式 HTTP+SSE 和 Streamable HTTP，优先选择 `bailian_web_search`，支持 `query`、`search_query` 或 `q` 参数，并在工具支持时传入 `count=5`。网页摘要为空的结果被过滤，保留的网页与本地结果统一精排。服务故障通过错误事件返回。

2026-11-17 实测发现当前百炼 `/sse` 地址返回空事件流，适配器会在握手阶段转到同服务的 `/mcp`。真实调用得到 5 条带链接的网页结果，报告位于 `temp-files/web-search-alignment-20261117.json`。

真实 HTTP 流式验收可执行下列命令。先单独启动 Web 服务，再将端口和确认产品替换为本机实际值。

```powershell
uv run python -m app.tool.verify_query --stream --web --base-url http://127.0.0.1:8000 --confirm-product "H3C LA2608 室内无线网关" --output temp-files/query-stream-verification.json
```

该模式验证产品澄清及确认、两轮回答、SSE 事件和完整增量、MongoDB 历史、Milvus 原文回读，以及引用图片的 HTTP 访问。

2026-11-17 完整验收通过，报告为 `temp-files/query-document-alignment-20261117.json`。通过本机 Uvicorn 的真实 HTTP 请求完成一次产品澄清和两轮回答，MongoDB 回读 3 轮记录。每轮收到 14 条节点进度事件，分别收到 16、38 条模型增量，拼接内容与最终答案一致。首个 SSE 事件分别约 15、32 毫秒，两轮实际检索、CPU 重排和生成约 78、74 秒。两轮各返回 5 个来源，Milvus 原文回读及 7 个引用图片的 HTTP 访问均通过。联网结果参加精排，本次入选的最终来源均为本地手册。

68 项离线自动化测试通过，两份前端脚本语法检查通过。前端事件模拟验证了 SSE 文本渲染、节点进度、图片、会话保存及拖拽上传进度。按本次会话要求采用接口与脚本测试，桌面和移动端浏览器视觉效果未重新验收。
