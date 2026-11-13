# -*- coding: utf-8 -*-
"""AI 代理：通过 API 调用大模型完成组题与复盘分析（OpenAI 兼容接口）"""

import json
import re
import threading

from openai import OpenAI

import config

_client = None
_client_settings = None
_client_lock = threading.Lock()


def _get_client():
    global _client, _client_settings
    with _client_lock:
        settings = (config.API_KEY, config.BASE_URL, config.MODEL)
        if not settings[0] or settings[0] == "YOUR_API_KEY":
            raise RuntimeError(
                "尚未配置 API Key：请打开「设置」填写，"
                "或设置环境变量 CCPC_AI_API_KEY。"
            )
        if _client is None or _client_settings != settings:
            kwargs = {"api_key": settings[0], "timeout": 60, "max_retries": 1}
            if settings[1]:
                kwargs["base_url"] = settings[1]
            _client = OpenAI(**kwargs)
            _client_settings = settings
        return _client


def _parse_json(content):
    """鲁棒解析模型输出：容忍 ```json 围栏、前后多余文字、尾逗号。"""
    content = (content or "").strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if fence:
        content = fence.group(1).strip()

    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start:end + 1]

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", content)  # 去掉尾逗号后重试
        return json.loads(fixed)


def ask_gpt(prompt, system=None):
    """调用大模型并要求返回 JSON 对象。"""
    response = _get_client().chat.completions.create(
        model=config.MODEL,
        messages=[
            {
                "role": "system",
                "content": system
                or "你是一名 CCPC 校队教练，擅长分析竞赛训练数据，所有输出必须是严格的 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return _parse_json(response.choices[0].message.content)


def ask_gpt_text(prompt, system=None):
    """调用大模型并返回原始文本输出（用于翻译、总结等非 JSON 任务）。"""
    response = _get_client().chat.completions.create(
        model=config.MODEL,
        messages=[
            {
                "role": "system",
                "content": system or "你是算法竞赛助手。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        timeout=180,
    )
    return (response.choices[0].message.content or "").strip()
