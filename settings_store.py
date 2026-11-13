"""连接配置的校验和原子写入，不依赖 GUI 或网络。"""

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


DEFAULTS = {
    "API_KEY": "",
    "BASE_URL": "https://api.deepseek.com",
    "MODEL": "deepseek-chat",
    "CF_HANDLE": "",
    "CF_PASSWORD": "",
    "CF_API_KEY": "",
    "CF_API_SECRET": "",
}
ENV_NAMES = {
    key: ("CCPC_AI_" + key if key in ("API_KEY", "BASE_URL", "MODEL") else key)
    for key in DEFAULTS
}


def read_settings(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        # 错误信息不引用文件内容，避免显示密钥。
        raise ValueError("无法读取 settings.json，请检查文件权限和 JSON 格式。") from exc
    if not isinstance(data, dict):
        raise ValueError("settings.json 必须是 JSON 对象。")
    if any(key in data and not isinstance(data[key], str) for key in DEFAULTS):
        raise ValueError("settings.json 中的连接配置必须填写字符串。")
    return {key: data[key] for key in DEFAULTS if key in data}


def effective_settings(local):
    return {
        key: os.environ.get(ENV_NAMES[key], local.get(key, default))
        for key, default in DEFAULTS.items()
    }


def ai_is_configured(values):
    return bool(values["API_KEY"].strip() and values["API_KEY"].strip() != "YOUR_API_KEY"
                and values["MODEL"].strip())


def validate_settings(values):
    if not ai_is_configured(values):
        raise ValueError("请填写 API Key 和模型名称；暂不配置可点击「稍后设置」。")
    url = values["BASE_URL"].strip()
    if url:
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme in ("http", "https") and parsed.hostname
                     and parsed.port != 0 and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("服务地址须为完整的 http:// 或 https:// 地址，不含账号、密码或查询参数。")


def write_settings(path, values):
    """先写临时文件再替换，失败时保留原文件。"""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
