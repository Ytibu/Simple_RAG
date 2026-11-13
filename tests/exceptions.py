import app.processor.import_process.exceptions as Ee

try:
    raise Ee.LLMError("错误")
except Exception as e:
    print(e)