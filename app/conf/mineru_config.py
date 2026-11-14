"""MinerU 云 API 配置，凭据仅从环境读取。"""
from dataclasses import dataclass, field
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class MineruConfig:
    base_url: str = field(default_factory=lambda: os.getenv("MINERU_BASE_URL", ""))
    api_token: str = field(default_factory=lambda: os.getenv("MINERU_API_TOKEN", ""), repr=False)
    model_version: str = field(default_factory=lambda: os.getenv("MINERU_MODEL_VERSION", "vlm"))
    request_timeout: float = field(default_factory=lambda: float(os.getenv("MINERU_REQUEST_TIMEOUT", "60")))
    task_timeout: float = field(default_factory=lambda: float(os.getenv("MINERU_TASK_TIMEOUT", "600")))
    poll_interval: float = field(default_factory=lambda: float(os.getenv("MINERU_POLL_INTERVAL", "3")))

    @property
    def api_key(self):
        """兼容原配置的读取名称，实际使用 API token。"""
        return self.api_token


mineru_config = MineruConfig()
