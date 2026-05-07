"""GLM API 封装：调用智谱 AI 对话补全接口"""

import os
import json
import time
import logging
import requests
import yaml
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'settings.yaml')
with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
    _CONFIG = yaml.safe_load(f)

_API_ENDPOINT = _CONFIG['api']['endpoint']


def _load_api_key() -> str:
    """从配置文件指定的密钥文件中加载 API Key"""
    key_file = _CONFIG['api']['key_file']
    # 相对于项目根目录
    root = os.path.join(os.path.dirname(__file__), '..', '..')
    key_path = os.path.join(root, key_file)
    with open(key_path, 'r', encoding='utf-8') as f:
        return f.read().strip()


class GLMClient:
    """智谱 GLM API 客户端"""

    def __init__(
        self,
        model: Optional[str] = None,
        max_retries: int = 3,
        api_key: Optional[str] = None,
    ):
        self.api_key = (api_key or "").strip() or _load_api_key()
        self.model = model or _CONFIG['models']['candidate_model']
        self.max_retries = max_retries
        self.endpoint = _API_ENDPOINT

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.9,
        max_tokens: int = 2048,
        response_format: Optional[Dict] = None,
    ) -> Optional[str]:
        """发送对话请求

        Args:
            messages: 对话消息列表
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            response_format: 响应格式，如 {"type": "json_object"}

        Returns:
            模型回复文本，失败返回 None
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            payload["response_format"] = response_format

        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )

                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning(f"限流，等待 {wait}s 后重试")
                    time.sleep(wait)
                    continue

                if resp.status_code != 200:
                    logger.error(f"API 返回错误 {resp.status_code}: {resp.text}")
                    if attempt < self.max_retries - 1:
                        time.sleep(1)
                        continue
                    return None

                data = resp.json()
                content = data['choices'][0]['message']['content']
                return content

            except requests.exceptions.Timeout:
                logger.warning(f"请求超时，第 {attempt+1} 次")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.error(f"解析响应失败: {e}")
                return None
            except requests.exceptions.RequestException as e:
                logger.error(f"请求异常: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

        return None

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.9,
        max_tokens: int = 2048,
    ) -> Optional[Dict]:
        """发送对话请求并解析 JSON 响应"""
        content = self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        if content is None:
            return None

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 JSON
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1:
                try:
                    return json.loads(content[start:end+1])
                except json.JSONDecodeError:
                    pass
            logger.error(f"无法解析 JSON 响应: {content[:200]}")
            return None
