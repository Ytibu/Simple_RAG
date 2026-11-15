# 知识库查询流程

## 功能与调用关系

入口为 `app/processor/query_process/agent/kb_query_workflow.py`，原 V2 入口复用同一实现。查询只读取 Milvus，不创建集合、不改动已导入数据。

| 节点 | 职责 |
| --- | --- |
| `node_item_name_confirm` | 根据最近会话改写问题，搜索主体集合，由 LLM 从实际候选中选择主体；无文档、型号不存在或需澄清时提前返回 |
| `node_search_embedding` | BGE-M3 查询编码，在指定主体范围内进行 dense COSINE + sparse IP 混合检索 |
| `node_search_embedding_hyde` | 生成假设性手册内容，执行第二路混合检索；假设文本不进入最终回答上下文 |
| `node_web_search_mcp` | 显式启用时调用配置的 MCP SSE 搜索服务，默认关闭 |
| `node_rrf` | 等待所有分支完成，按文档 ID 和切片主键去重，执行 RRF 融合 |
| `node_rerank` | 对融合后的候选打分，保留最终 Top-K |
| `node_answer_output` | 仅根据入选原文生成带 `[1]` 等引用的回答；资料不足时明确说明 |

普通召回与 HyDE 各取 12 个候选，融合后最多保留 12 个，默认最终返回 5 个切片。查询使用与导入一致的 BGE-M3 编码器和集合，数据库异常直接报错，不伪装成空库。各召回分支只返回自身字段，汇合边显式等待全部分支，避免状态冲突或提前生成答案。

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
- MongoDB 的 `query_turns` 集合每条保存一整轮问题、回答和引用；加载最近 5 轮作为上下文。失败的查询不写入成功记录。原有历史集合不受影响。

当前本机验证使用已有的 BGE-M3 与 bge-reranker-large，均为 CPU，无新增模型下载。长切片本地重排约需一分钟以上；首次查询还包含模型加载耗时。查询服务按请求串行运行，适合当前本机单用户调试。

## Web 与接口

启动 `uv run uvicorn app.main:app`，访问 `http://127.0.0.1:8000/query`。页面支持连续提问、查看引用原文和开始新会话；新会话不删除旧会话记录。

| 接口 | 用途 |
| --- | --- |
| `POST /api/queries` | 查询并保存成功结果 |
| `GET /api/queries/{session_id}/history` | 按时间顺序返回最近 20 轮 |
| `DELETE /api/queries/{session_id}/history` | 删除指定会话历史，不删除知识库 |

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

响应包含 `session_id`、`task_id`、`answer`、`rewritten_query`、`item_names`、`item_candidates`、`sources` 和 `reranker_used`。继续提问时传回相同的 `session_id`。来源保留引用编号、切片主键、文档 ID、标题、元数据与实际送入模型的原文。

`question` 长度为 1–4000，`top_k` 为 1–12；会话 ID 仅允许字母、数字、下划线和连字符。无效输入返回 422，模型或存储服务错误返回 503。接口响应不包含密钥或底层异常详情。

## CLI 与验证

```powershell
uv run python -m app.processor.query_process.agent.kb_query_workflow "H3C LA2608支持哪些无线控制器？"
uv run python -m app.processor.query_process.agent.kb_query_workflow "LA2608配置要求是什么？" --top-k 3 --no-hyde
uv run python -m unittest discover -s tests -p "test_*.py" -q
uv run python -m app.tool.verify_query
```

CLI 的 `--stream` 输出节点完成状态，非逐字输出。真实验收脚本经 FastAPI 接口连续提问两轮，要求实际使用本地重排，并核对 MongoDB 历史以及引用切片的 Milvus 回读；报告保存到 `temp-files/query-verification.json`。仅运行单元测试不会调用模型 API 或向数据库写入。

本次自动化测试覆盖查询图汇合、RRF 去重、主体澄清、无文档、历史改写、来源约束、重排分数、主键适配、服务失败、HTTP 参数与历史接口、MCP SSE 握手。完整测试共 52 项。

首轮真实验收查询“恢复出厂设置”及其注意事项，两轮均返回 5 个原文切片，MongoDB 回读到 2 轮，切片内容与 Milvus 完全一致；手册缺少该操作说明，模型明确回答资料不足。记录见 `temp-files/query-verification.json`。

第二组真实验收查询连接网络和支持厂商，再追问配置前提：回答明确给出“通过 3G/4G 网络连接，仅支持 H3C 无线控制器”，引用第 4 页原文；后续问题中的“它”正确改写为 LA2608。两轮均使用本地重排，返回各 5 个切片，MongoDB 和 Milvus 回读均通过。记录见 `temp-files/query-positive-verification.json`。上述两组完整查询经 FastAPI TestClient 执行了真实路由、模型与存储调用；另对运行中的 Uvicorn 发送真实 HTTP 请求，确认历史接口返回 200、2 轮记录，空问题返回 422，OpenAPI 返回 200。浏览器操作按用户要求停止，后续均为接口测试。

## 可选联网搜索边界

`use_web=true` 或 CLI `--web` 才调用现有 `MCP_DASHSCOPE_BASE_URL` 与 `OPENAI_API_KEY`。适配器实现旧式 HTTP+SSE 的初始化、工具发现与搜索调用，支持参数名 `query`、`search_query` 或 `q`，不支持其他自定义工具协议。联网结果单独标记来源后参与融合；服务故障会让本次查询返回错误。

默认知识库检索和回答已使用真实服务验证；MCP 传输目前仅通过模拟协议测试，未验证公网服务权限与返回质量。
