# -*- coding: utf-8 -*-
"""全局配置：AI API、模型、文件路径、组题参数"""

import os
import sys

# AI 与账号配置通过首次配置窗口管理，也支持环境变量。
# 内置连接默认值见 settings_store.py；此文件保留比赛与路径参数。

# ----------------- 文件路径 -----------------
# 打包成 exe 后取 exe 所在目录（memory/ 需与 exe 放在一起）；源码运行取项目目录
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 队伍长期记忆（薄弱点 / 优势 / 技能）
MEMORY_FILE = os.path.join(_BASE_DIR, "memory", "team_memory.json")

# 每场模拟赛的历史记录
HISTORY_FILE = os.path.join(_BASE_DIR, "memory", "history.json")

# 赛后复盘总结输出的目录
SUMMARY_DIR = os.path.join(_BASE_DIR, "memory", "summaries")

# CCPC 真题题库（由 build_problem_bank.py 生成，组题时 AI 只从中选题）
BANK_FILE = os.path.join(_BASE_DIR, "memory", "problem_bank.json")

# 当前比赛的本地提交记录（自动存档，供复盘与历史追溯）
SUBMIT_LOG_FILE = os.path.join(_BASE_DIR, "memory", "submissions.json")

# 当前场次的赛卷存档（生成即落盘：程序意外关闭后仍可恢复或补复盘）
CURRENT_CONTEST_FILE = os.path.join(_BASE_DIR, "memory", "current_contest.json")

# ----------------- 组题参数 -----------------
MIN_PROBLEM_COUNT = 10
MAX_PROBLEM_COUNT = 12
DEFAULT_PROBLEM_COUNT = 12

# ----------------- 计时器 -----------------
# 模拟赛时长（分钟），默认 5 小时
CONTEST_DURATION_MINUTES = 5 * 60

# 抓取评测结果的时间宽限（分钟）：
# 比赛时长到点后再宽限这么久之内的提交仍计入本场，
# 用于兜住"倒计时归零那一刻正在收尾提交"的压哨 AC；
# 超出正式比赛时长的提交会在备注里标注「超出比赛时长 N 分钟」，复盘时可见。
# 设为 0 表示严格按比赛时长截断。
VERDICT_GRACE_MINUTES = 2

# ----------------- 难度揭晓（模拟 CCPC 新规） -----------------
# 参考新规：赛前不暴露题目难度，到相应时间点才揭晓
# 题目按本场 AC 队伍数相对排名分档（1=签到题 … 5=困难题），值为揭晓时间（分钟）
DIFFICULTY_REVEAL_MINUTES = {
    1: 50,    # 签到题：50 分钟
    2: 90,    # 简单题
    3: 210,    # 中等题
    4: 240,   # 较难题
    5: 240,   # 困难题
}

# 连接配置从独立文件读取；格式错误时仍启动 GUI，允许用户修复。
from settings_store import effective_settings, read_settings

_SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")
SETTINGS_ERROR = ""
LOCAL_SETTINGS = {}


def apply_connection_settings(local, session_password=None):
    global LOCAL_SETTINGS, SETTINGS_ERROR
    LOCAL_SETTINGS = dict(local)
    values = effective_settings(local)
    if session_password is not None and "CF_PASSWORD" not in os.environ:
        values["CF_PASSWORD"] = session_password
    globals().update(values)
    SETTINGS_ERROR = ""


try:
    apply_connection_settings(read_settings(_SETTINGS_FILE))
except ValueError as _exc:
    SETTINGS_ERROR = str(_exc)
    globals().update(effective_settings({}))
