# Repository Guidelines

## 项目结构与模块职责

- `app/main.py` 注册 FastAPI 页面与接口。`app/api/` 定义导入、查询和会话历史接口，`app/services/` 编排导入任务与查询会话，`app/web/` 提供 HTML 与静态资源。
- `app/processor/import_process/` 实现 LangGraph 导入工作流，涵盖 PDF 解析、图片处理、切片、商品名识别、向量化和 Milvus 入库。
- `app/processor/query_process/` 实现 LangGraph 查询工作流，涵盖混合检索、重排和引用生成。Web 查询由 `app/services/queries.py` 调用 `KBQueryWorkflow`。
- `app/clients/` 封装 Milvus、MinIO、MongoDB、MinerU 和网络搜索调用。`app/lm/` 负责语言模型、嵌入和重排序调用。
- `app/conf/` 与 `app/processor/import_process`：服务与导入配置；`app/core/`、`app/utils/`：日志和公共工具。
- `app/tool/` 存放模型下载和真实服务验证脚本，`tests/` 包含离线自动化测试与手动验证脚本。环境、架构及流程说明见 [README](README.md)、[项目结构](docs/PROJECT_STRUCTURE.md)、[导入流程](docs/IMPORT_WORKFLOW.md)和[查询流程](docs/QUERY_WORKFLOW.md)。
- `logs/`、`volumes/`、`temp-files/` 为本地运行数据，不应提交。

## 环境与开发命令

项目要求 Python `>=3.11,<3.12`，`.python-version` 指定 `3.11.16`。在仓库根目录执行：

- `uv sync --locked`：按 `uv.lock` 同步依赖。
- `uv run uvicorn app.main:app`：启动 Web 服务，本机端口为 8000，页面及 API 文档路径为 `/import`、`/query` 和 `/docs`。
- `docker compose -f docs/compose.infra.yml config --quiet`：校验基础设施配置。
- `docker compose -f docs/compose.infra.yml up -d`：启动基础设施；首次执行可能下载镜像。
- `uv run python -m app.processor.import_process.agent.kb_import_workflow path/to/manual.pdf`：执行一次文件导入。先配置模型和存储服务；该命令会产生外部请求和入库操作，可添加 `--stream` 查看进度。
- `uv run python -m app.processor.query_process.agent.kb_query_workflow "设备如何配置？"`：使用已入库的资料执行一次查询，需要配置相关模型和检索服务。

HTML 页面由 FastAPI 同源提供，无需单独构建前端。安装依赖或下载模型前，先检查系统环境并确认使用方案。

## 编码风格与命名

使用四空格缩进；模块、函数及变量采用 `snake_case`，类采用 `PascalCase`，常量采用 `UPPER_SNAKE_CASE`。新增流程节点沿用 `node_*.py` 命名，并同步检查工作流注册、路由和状态字段。文档、注释及说明优先中文，标识符使用英文。沿用邻近代码的类型注解与文档字符串风格；目前未配置 Black、Ruff 等格式化或 lint 工具。

## 测试与验证

使用标准库 `unittest`。按需运行以下离线测试：

- 导入流程：`uv run python -m unittest discover -s tests -p test_import_workflow.py -v`
- 查询工作流、API 与会话历史：`uv run python -m unittest discover -s tests -p test_query_workflow.py -v`
- 网络搜索客户端：`uv run python -m unittest discover -s tests -p test_web_search.py -v`

新增自动化测试放在 `tests/`，并说明运行命令和依赖。`tests/env.py` 是环境变量演示，可能输出 API 密钥，避免执行该脚本或分享其输出。

修改导入逻辑时，用小型 PDF 或 Markdown 验证路由、切片、嵌入和入库。修改查询逻辑时，用已知资料验证检索、答案与引用，并按改动范围检查会话行为。记录输入、预期、实际结果及未验证项。需要真实服务联调时，参照流程文档中的 `app.tool.verify_import` 和 `app.tool.verify_query`，记录是否实际调用模型和访问数据库。

## 提交与 Pull Request

建议使用 `feat:`、`fix:`、`docs:` 等前缀，加简洁中文说明。提交前确认范围，保留无关的已有改动；未经授权不自动提交。

PR 应说明变更目的、涉及模块、配置变化、验证命令及结果，并关联已有问题。涉及工作流时附脱敏输入和结果摘要；依赖变化需同步维护 `pyproject.toml` 与 `uv.lock`。

## 配置与敏感信息

以 `.env.example` 为模板创建本地 `.env`，调整服务地址、模型路径和设备配置。不要提交真实密钥、私有文档或模型缓存。Compose 默认凭据仅适合本地开发；共享部署前更换凭据并检查端口暴露范围。
