# README.md

## Web 服务

在仓库根目录执行 `uv run uvicorn app.main:app`，打开 [导入页面](http://127.0.0.1:8000/import) 或 [API 文档](http://127.0.0.1:8000/docs)。模块职责和导入接口见 [项目结构](PROJECT_STRUCTURE.md)。

## 服务器创建
- 运行compose.infra.yaml，直接配置docker中的容器，milvus-standalone-v2.5.5.yaml为下载的官方配置不直接使用，作为compose.infra配置的参考
## 文件导入

节点职责、运行命令与验证边界见 [文件导入流程](IMPORT_WORKFLOW.md)。

## 知识库查询

访问 [查询页面](http://127.0.0.1:8000/query)，或调用 `POST /api/queries`。支持主体确认、混合检索、HyDE、RRF、本地重排、带引用回答与 MongoDB 连续会话，配置与验收见 [查询流程](QUERY_WORKFLOW.md)。
