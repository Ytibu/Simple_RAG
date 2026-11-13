"""保留旧入口名称，图构建统一交给 KBImportWorkflow。"""
from app.processor.import_process.agent.kb_import_workflow import KBImportWorkflow, main

workflow = KBImportWorkflow().workflow
kb_import_app = workflow.compile()

if __name__ == "__main__":
    main()
