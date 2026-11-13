# -*- coding: utf-8 -*-
"""Codeforces 真实提交：Playwright 驱动本机 Edge 浏览器登录与提交。

Codeforces 对脚本请求启用 Cloudflare 挑战（requests/cloudscraper 均被 403），
因此改用真实浏览器（系统自带 Edge）完成登录与提交，
登录态保存在 memory/edge_profile（含会话 Cookie，请勿外传）。
"""

import os
import re

import config

BASE = "https://codeforces.com"

# 提交页语言列表解析失败时的回退表（CF 常用语言 -> programTypeId）
FALLBACK_LANGUAGES = {
    "GNU G++20 (64bit, winlibs)": "91",
    "GNU G++17 7.3.0": "54",
    "GNU G++14 6.4.0": "50",
    "Python 3": "31",
    "PyPy 3 (64bit)": "70",
}

# CF 判定 -> 本地结果
VERDICT_MAP = {
    "OK": "AC",
    "WRONG_ANSWER": "WA",
    "TIME_LIMIT_EXCEEDED": "TLE",
    "MEMORY_LIMIT_EXCEEDED": "MLE",
    "RUNTIME_ERROR": "RE",
    "COMPILATION_ERROR": "CE",
    "PRESENTATION_ERROR": "PE",
    "CHALLENGED": "被Hack",
    "SKIPPED": "重复提交",
    "TESTING": "判定中",
}


def fetch_verdicts(handle, problems, since_ts=0, until_ts=0):
    """通过 CF 官方 API 抓取用户在指定题目上的提交（无需登录）。

    problems: 本场题目列表（含 letter 与 url）。
    只返回提交时间在 [since_ts, until_ts] 区间内的记录（until_ts=0 表示无上限）。
    返回 {letter: [(结果, 提交时间戳秒), ...]}（按时间正序），失败抛异常。
    """
    import requests

    targets = {}
    for p in problems:
        cid, code, _page = parse_problem_url(p.get("url", ""))
        if cid and code:
            targets[p.get("letter")] = (int(cid), code)
    if not targets:
        return {}

    resp = requests.get(
        f"{BASE}/api/user.status",
        params={"handle": handle, "from": 1, "count": 1000},
        timeout=20,
    )
    try:
        data = resp.json()
    except ValueError as e:  # noqa: BLE001
        raise RuntimeError("CF API 返回格式异常") from e
    if data.get("status") != "OK":
        raise RuntimeError(data.get("comment", "CF API 返回错误"))

    out = {}
    for sub in data.get("result", []):
        ts = sub.get("creationTimeSeconds", 0)
        if ts < since_ts:
            continue
        if until_ts and ts > until_ts:
            continue
        cid = sub.get("contestId")
        code = sub.get("problem", {}).get("index", "")
        for letter, (tcid, tcode) in targets.items():
            if cid == tcid and code == tcode:
                verdict = sub.get("verdict", "TESTING")
                out.setdefault(letter, []).append(
                    (VERDICT_MAP.get(verdict, verdict), ts)
                )
                break
    for letter in out:
        out[letter].sort(key=lambda x: x[1])  # 按时间正序
    return out

# 固定浏览器用户目录（登录态与 Cloudflare Cookie 持久保存在这里，勿外传）
_PROFILE_DIR = os.path.join(config._BASE_DIR, "memory", "edge_profile")

# Cloudflare 挑战需要可见浏览器，勿改为无头模式
_HEADLESS = False

_LANGS_CACHE = None


def has_session():
    """是否已有保存的浏览器登录目录。"""
    return os.path.exists(os.path.join(_PROFILE_DIR, "Default"))


def get_language_id(name):
    """优先用登录时解析到的语言表，回退到内置常用表。"""
    if _LANGS_CACHE and name in _LANGS_CACHE:
        return _LANGS_CACHE[name]
    return FALLBACK_LANGUAGES.get(name)


def _launch(pw):
    """启动固定用户目录的 Edge：登录态与 Cookie 持久保存，下次自动登录。"""
    os.makedirs(_PROFILE_DIR, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        _PROFILE_DIR,
        channel="msedge",
        headless=_HEADLESS,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 800},
    )


def _wait_logged(page, timeout_ms):
    try:
        page.wait_for_selector('a[href*="logout"]', timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        return False


def _contest_id_from(submit_url):
    m = re.search(r"/(?:gym|contest)/(\d+)/", submit_url)
    return m.group(1) if m else None


def _register_virtual(page, contest_id):
    """自动注册 gym 比赛的 virtual participation（提交前必需），成功返回 True。"""
    try:
        page.goto(
            f"{BASE}/contestRegistration/{contest_id}/virtual",
            wait_until="domcontentloaded", timeout=60000,
        )
        page.wait_for_selector('input[type="submit"]', timeout=15000)
        page.click('input[type="submit"]')
        try:
            page.wait_for_url(lambda u: "Registration" not in u, timeout=30000)
        except Exception:  # noqa: BLE001
            pass
        return "Registration" not in page.url
    except Exception:  # noqa: BLE001
        return False


def login(handle, password):
    """打开 Edge 并确保登录：已登录过则自动复用登录态。

    登录态保存在固定浏览器目录 memory/edge_profile 中，
    首次手动登录（含 Cloudflare 验证）后，之后可自动登录。
    """
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            ctx = _launch(pw)
            page = ctx.new_page()
            # 1) 先尝试自动登录（用户目录里已有登录态）
            page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
            if _wait_logged(page, 10000):
                return True, "已自动登录（复用已保存的登录态）"
            # 2) 打开登录页，预填账号密码（不自动点击，避免触发验证循环）
            page.goto(f"{BASE}/enter", wait_until="domcontentloaded", timeout=60000)
            if handle:
                try:
                    page.wait_for_selector('input[name="handleOrEmail"]', timeout=60000)
                    page.fill('input[name="handleOrEmail"]', handle)
                    if password:
                        page.fill('input[name="password"]', password)
                except Exception:
                    # 表单被 Cloudflare 刷新，用户手动输入即可
                    pass
            # 3) 等待用户在浏览器中完成登录
            if _wait_logged(page, 300000):
                return True, "登录成功，登录态已保存"
            return False, "登录超时：请在浏览器窗口中手动完成登录（含 Cloudflare 验证）"
    except Exception as e:  # noqa: BLE001
        return False, f"登录失败：{e}"


def ensure_login(handle="", password=""):
    """确保已登录；已保存登录态直接复用，否则用给定凭据登录。"""
    if has_session():
        return True, "已登录"
    return login(handle, password)


def parse_problem_url(url):
    """从题目链接解析 (contest_id, problem_code, 提交页 URL)。"""
    m = re.search(r"/gym/(\d+)/problem/(\w+)", url)
    if m:
        return m.group(1), m.group(2), f"{BASE}/gym/{m.group(1)}/submit"
    m = re.search(r"/problemset/problem/(\d+)/(\w+)", url)
    if m:
        return m.group(1), m.group(2), f"{BASE}/contest/{m.group(1)}/submit"
    m = re.search(r"/contest/(\d+)/problem/(\w+)", url)
    if m:
        return m.group(1), m.group(2), f"{BASE}/contest/{m.group(1)}/submit"
    return None, None, None


def fetch_languages(submit_url):
    """用浏览器打开提交页解析可选语言列表；失败返回 None。"""
    global _LANGS_CACHE
    if not submit_url or not has_session():
        return None
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            ctx = _launch(pw)
            page = ctx.new_page()
            page.goto(submit_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('select[name="programTypeId"]', timeout=60000)
            langs = {}
            for opt in page.query_selector_all('select[name="programTypeId"] option'):
                value = opt.get_attribute("value")
                name = (opt.inner_text() or "").strip()
                if value and name:
                    langs[name] = value
            if langs:
                _LANGS_CACHE = langs
            return langs or None
    except Exception:  # noqa: BLE001
        return None


def submit_solution(submit_url, problem_code, lang_id, source):
    """用已保存的登录态打开提交页并提交代码。返回 (成功?, 消息)。"""
    from playwright.sync_api import sync_playwright

    if not has_session():
        return False, "尚未登录：请先点击「登录 CF」"
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw)
            page = ctx.new_page()
            page.goto(submit_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('select[name="programTypeId"]', timeout=60000)
            if "/enter" in page.url:
                return False, "未登录：请先点击「登录 CF」"
            # gym 比赛需要先注册（virtual participation），自动完成
            if "registered for the contest" in page.content():
                cid = _contest_id_from(submit_url)
                ok_reg = bool(cid) and _register_virtual(page, cid)
                if ok_reg:
                    page.goto(submit_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector('select[name="programTypeId"]', timeout=60000)
                    ok_reg = "registered for the contest" not in page.content()
                if not ok_reg:
                    return False, (
                        "该比赛需要先报名：请在浏览器窗口中手动完成注册，"
                        "然后再点「提交到 CF」"
                    )
            page.select_option('select[name="programTypeId"]', str(lang_id))
            page.fill('textarea[name="source"]', source)
            page.click('input[value="Submit"]')
            try:
                page.wait_for_url(lambda u: "/submit" not in u, timeout=30000)
            except Exception:  # noqa: BLE001
                pass
            if "/enter" in page.url:
                return False, "登录态已过期：请重新点击「登录 CF」"
            err = page.query_selector("span.error")
            if err and err.is_visible():
                return False, f"提交失败：{err.inner_text().strip()}"
            if page.url.rstrip("/").endswith("/submit"):
                return False, "提交失败：请检查题号、语言与代码"
            return True, "提交成功，代码已进入评测队列"
    except Exception as e:  # noqa: BLE001
        return False, f"提交失败：{e}"
