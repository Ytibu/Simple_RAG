# 知识库项目结构

```text
app/
├── main.py                 # FastAPI 应用入口
├── api/imports.py          # 上传与进度 API
├── api/queries.py          # 查询与会话历史 API
├── services/imports.py     # 批量暂存、任务状态、工作流调用
├── services/queries.py     # 查询图执行、上下文加载与保存
├── web/
│   ├── routes.py           # HTML 页面路由
│   ├── pages/import.html
│   ├── pages/query.html
│   └── static/             # 页面 CSS 与 JavaScript
├── processor/
│   ├── import_process/     # 现有 LangGraph 导入图、状态与节点
│   └── query_process/      # 查询图、混合检索、重排与引用生成
├── clients/                # 外部服务客户端
├── lm/                     # 语言模型、向量化和重排序
├── conf/                   # 配置
└── core/                   # 日志
```

浏览器经同源 API 提交文件。API 校验请求并提交后台任务，导入服务将文件暂存至 `MD_ROOT_DIR`，然后调用 `KBImportWorkflow`。导入节点继续使用现有任务状态工具上报进度。CLI 导入命令见[文件导入流程](IMPORT_WORKFLOW.md)。

## 导入接口

| 路径 | 用途 |
| --- | --- |
| `GET /import` | 导入页面，支持同时选择多个 PDF 或 Markdown 文件 |
| `POST /api/imports` | 接收 multipart 表单字段 `files`，返回 HTTP 202 和按上传顺序排列的 `task_ids` |
| `GET /api/imports/{task_id}` | 返回任务状态、运行中节点、已完成节点和入库切片数 |

保留 `/import.html`、`/upload` 和 `/status/{task_id}` 作为旧路径。Web 启动入口为 `app.main:app`。

导入任务状态保存在当前进程中，使用单个 Uvicorn worker。进程重启后，页面需重新提交未完成的任务。暂存文件和解析结果按任务存放在 `MD_ROOT_DIR`，成功或失败后均保留，便于核对和排查。

当前入口供本机使用。现有 Markdown 图片节点会按文档中的引用读取本机图片，因此请只导入可信文档。

原文件默认仅暂存到本地。需要归档时，将 `MINIO_IMPORT_ARCHIVE_BUCKET` 设为已创建且关闭匿名读取的 MinIO 桶。归档失败会记录日志，导入图仍继续执行。

## 查询接口

`GET /query` 提供查询页面；`POST /api/queries` 执行查询并返回答案与原文引用。`GET /api/queries/{session_id}/history` 读取历史，`DELETE /api/queries/{session_id}/history` 清空指定会话。查询服务加载最近会话，调用 `KBQueryWorkflow`，成功后写入 MongoDB 的 `query_turns` 集合。节点职责、配置、CLI 与真实验收命令见[查询流程](QUERY_WORKFLOW.md)。
