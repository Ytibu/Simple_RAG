from app.processor.import_process import create_default_state, graph_default_state
from app.core.logger import logger

"""
测试
"""

# ❌ 缺少默认参数
# initial_state = ImportGraphState(
#     task_id="task_001",
#     local_file_path="万用表的使用.txt"
# )

# ✅ 创建默认状态
default_state = graph_default_state
print(default_state)
state = create_default_state(local_file_path="万用表RS-12的使用.pdf")
print(state)
print(default_state == state)

logger.info(state)