# Repository Guidelines

## 项目结构与模块职责

- `app/processor/import_process`：LangGraph 导入工作流；`nodes/node_*.py` 实现 PDF 解析、图片处理、切片、商品名识别、向量化及 Milvus 入库。
- `app/clients/`：Milvus、MinIO、MongoDB 客户端；`app/lm/`：语言模型、嵌入和重排序调用。
- `app/conf/` 与 `app/processor/import_process`：服务与导入配置；`app/core/`、`app/utils/`：日志和公共工具。
- `app/tool/`：模型下载脚本；`docs/`：环境说明及 Compose 配置；`tests`：手动验证脚本。
- `logs/`、`volumes/` 为本地运行数据，不应提交；目前没有独立的前端或静态资源目录。

## 环境与开发命令

项目要求 Python `>=3.11,<3.12`，`.python-version` 指定 `3.11.16`。在仓库根目录执行：

- `uv sync --locked`：按 `uv.lock` 同步依赖。
- `docker compose -f docs/compose.infra.yml config --quiet`：校验基础设施配置。
- `docker compose -f docs/compose.infra.yml up -d`：启动基础设施；首次执行可能下载镜像。
- `uv run python -m app.processor.import_process.agent.kb_import_workflow path/to/manual.pdf`：执行一次文件导入。先配置模型和存储服务；该命令会产生外部请求和入库操作，可添加 `--stream` 查看进度。

当前没有独立构建命令或 HTTP 服务启动入口。安装依赖或下载模型前，先检查系统环境并确认使用方案。

## 编码风格与命名

使用四空格缩进；模块、函数及变量采用 `snake_case`，类采用 `PascalCase`，常量采用 `UPPER_SNAKE_CASE`。新增流程节点沿用 `node_*.py` 命名，并同步检查工作流注册、路由和状态字段。文档、注释及说明优先中文，标识符使用英文。沿用邻近代码的类型注解与文档字符串风格；目前未配置 Black、Ruff 等格式化或 lint 工具。

## 测试与验证

使用标准库 unittest；运行 `uv run python -m unittest discover -s tests -p test_import_workflow.py -v` 验证离线导入流程。目前未设置覆盖率门槛。新增自动化测试建议放在 `tests`，并说明运行命令及依赖。`tests` 是环境变量演示，会输出 API 密钥，避免直接执行或分享其原始输出。

修改导入逻辑时，用小型 PDF 或 Markdown 验证路由、切片、嵌入及入库结果，并记录输入、预期、实际结果和未验证环节。语法检查不能替代模型与数据库的实际联调。

## 提交与 Pull Request

当前历史仅有 `init: 初始化依赖并搭建项目框架`，尚不足以形成完整规范。建议使用 `feat:`、`fix:`、`docs:` 等前缀，加简洁中文说明。提交前确认范围，保留无关的已有改动；未经授权不自动提交。

PR 应说明变更目的、涉及模块、配置变化、验证命令及结果，并关联已有问题。涉及工作流时附脱敏输入和结果摘要；依赖变化需同步维护 `pyproject.toml` 与 `uv.lock`。

## 配置与敏感信息

以 `.env.example` 为模板创建本地 `.env`，调整服务地址、模型路径和设备配置。不要提交真实密钥、私有文档或模型缓存。Compose 默认凭据仅适合本地开发；共享部署前更换凭据并检查端口暴露范围。
