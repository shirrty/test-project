"""配置：AnythingLLM API 基地址与密钥

通过环境变量读取：
- ANYTHINGLLM_API_BASE_URL：AnythingLLM 后端地址，默认 http://localhost:3001
- ANYTHINGLLM_API_KEY：Developer API 密钥，在 AnythingLLM UI 的「设置 → API」中生成
"""

import os

API_BASE_URL = os.environ.get(
    "ANYTHINGLLM_API_BASE_URL", "http://localhost:3001"
).rstrip("/")

API_KEY = os.environ.get("ANYTHINGLLM_API_KEY", "")

# 请求超时（秒）
TIMEOUT = float(os.environ.get("ANYTHINGLLM_TIMEOUT", "60"))
