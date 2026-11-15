"""保留原入口，统一使用已经验证的查询图。"""
from app.processor.query_process.agent.kb_query_workflow import KBQueryWorkflow, main

KBQueryWorkflowV2 = KBQueryWorkflow

if __name__ == "__main__":
    main()
