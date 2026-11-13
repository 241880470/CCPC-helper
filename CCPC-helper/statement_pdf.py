# -*- coding: utf-8 -*-
"""中文题面 PDF 生成：抓取 CF 题面 → AI 翻译 → 输出 ccpc_roundX.pdf"""

import json
import os

import cf_submit
import config
from AI_agent import ask_gpt_text

PDF_DIR = config._BASE_DIR
_COUNT_FILE = os.path.join(config._BASE_DIR, "memory", "round_count.json")

_SYSTEM = "你是算法竞赛题目翻译专家，将英文题面翻译成简体中文。"

_PROMPT_TMPL = """请将下面的英文题目完整翻译成简体中文（用于 CCPC 训练），并只输出如下 Markdown 格式：

# 中文标题

## 题目描述
（完整翻译题意、背景与目标）

## 输入格式
...

## 输出格式
...

## 样例
``` 样例输入
...
```

``` 样例输出
...
```

## 提示（如原文有范围、特殊说明则翻译）
...

要求：
1. 完整准确，不省略任何样例数据与数字。
2. 变量名、函数名保留原样。
3. 公式必须写成可直接阅读的文本，**禁止**输出 $、LaTeX 定界符和任何反斜杠命令：
   例如 1 ≤ T ≤ 100、10^9、3 × 10^5、求和号写为「Σ」、分数写为「a/b」。
4. 只输出上述 Markdown，不要任何解释。

英文题面：
{statement}"""

# 在浏览器里提取题面：公式图片替换为 $LaTeX$，去掉脚本样式
_EXTRACT_JS = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return '';
  const clone = el.cloneNode(true);
  clone.querySelectorAll('img').forEach(img => {
    const alt = (img.getAttribute('alt') || '').trim();
    img.replaceWith(alt ? '$' + alt + '$' : '');
  });
  clone.querySelectorAll('script,style').forEach(n => n.remove());
  return clone.innerText;
}"""


def _next_round():
    """读取并递增 round 计数器，返回本次编号。"""
    count = 1
    if os.path.exists(_COUNT_FILE):
        try:
            with open(_COUNT_FILE, "r", encoding="utf8") as f:
                data = json.load(f)
            count = int(data.get("count", 0)) + 1
        except (json.JSONDecodeError, OSError, ValueError):
            count = 1
    os.makedirs(os.path.dirname(_COUNT_FILE), exist_ok=True)
    with open(_COUNT_FILE, "w", encoding="utf8") as f:
        json.dump({"count": count}, f)
    return count


async def _fetch_statements_async(problems, progress_cb=None):
    """并发抓取每题英文题面（最多 4 道同时），返回 {letter: 文本}。"""
    import asyncio

    from playwright.async_api import async_playwright

    results = {}
    total = len(problems)
    done = 0

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            cf_submit._PROFILE_DIR,
            channel="msedge",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        sem = asyncio.Semaphore(4)

        async def grab(p):
            page = await ctx.new_page()
            try:
                url = p.get("url", "")
                letter = p.get("letter", "?")
                if not url:
                    return letter, ""
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Cloudflare 托管挑战一般会自动通过，等待片刻
                for _ in range(6):
                    if "Just a moment" not in (await page.title() or ""):
                        break
                    await page.wait_for_timeout(2500)
                if "Just a moment" in (await page.title() or ""):
                    return letter, "[题面抓取失败：Cloudflare 验证未通过]"
                await page.wait_for_selector(".problem-statement", timeout=20000)
                text = await page.evaluate(_EXTRACT_JS, ".problem-statement")
                return letter, (text or "").strip()
            except Exception as e:  # noqa: BLE001
                return p.get("letter", "?"), f"[题面抓取失败：{e}]"
            finally:
                await page.close()

        async def limited(p):
            async with sem:
                return await grab(p)

        tasks = [asyncio.create_task(limited(p)) for p in problems]
        for coro in asyncio.as_completed(tasks):
            letter, text = await coro
            results[letter] = text
            done += 1
            if progress_cb:
                progress_cb(done, total)
    return results


def _fetch_statements(problems, progress_cb=None):
    """同步入口：内部并发抓取。"""
    import asyncio

    return asyncio.run(_fetch_statements_async(problems, progress_cb))


def _translate(statement):
    if not statement or statement.startswith("[题面抓取失败"):
        return ""  # 题面读不出来就不翻译
    try:
        text = ask_gpt_text(_PROMPT_TMPL.format(statement=statement), system=_SYSTEM)
        return text or statement
    except Exception:  # noqa: BLE001
        return statement  # 翻译失败时保留原文


def _register_font():
    """注册中文字体，返回字体名；找不到时回退 Helvetica。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path, idx in (
        ("C:/Windows/Fonts/msyh.ttc", 0),
        ("C:/Windows/Fonts/simhei.ttf", None),
        ("C:/Windows/Fonts/simsun.ttc", 0),
    ):
        if not os.path.exists(path):
            continue
        try:
            if idx is None:
                pdfmetrics.registerFont(TTFont("CJK", path))
            else:
                pdfmetrics.registerFont(TTFont("CJK", path, subfontIndex=idx))
            return "CJK"
        except Exception:  # noqa: BLE001
            continue
    return "Helvetica"


def _flush(story, styles, buf, kind):
    from reportlab.platypus import Paragraph

    if buf:
        story.append(Paragraph("<br/>".join(buf), styles[kind]))


def _render_markdown(story, styles, text):
    """极简 markdown 渲染：# 标题、## 小节、``` 代码块、普通段落。"""
    from reportlab.platypus import Paragraph, Spacer

    code = False
    buf = []

    def esc(line):
        return line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if s.startswith("```"):
            _flush(story, styles, buf, "code" if code else "body")
            buf = []
            code = not code
            continue
        if code:
            buf.append(esc(line))
            continue
        if s.startswith("# "):
            _flush(story, styles, buf, "body")
            buf = []
            story.append(Paragraph(esc(s[2:]), styles["h1"]))
        elif s.startswith("## "):
            _flush(story, styles, buf, "body")
            buf = []
            story.append(Paragraph(esc(s[3:]), styles["h2"]))
        elif not s:
            _flush(story, styles, buf, "body")
            buf = []
        else:
            buf.append(esc(line))
    _flush(story, styles, buf, "code" if code else "body")
    story.append(Spacer(1, 8))


def build_pdf(contest, statements, path):
    """把中文题面渲染成 PDF。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    font = _register_font()
    styles = {
        "title": ParagraphStyle(
            "title", fontName=font, fontSize=18, leading=26,
            spaceAfter=10, alignment=1,
        ),
        "h1": ParagraphStyle(
            "h1", fontName=font, fontSize=14, leading=20,
            spaceBefore=8, spaceAfter=4,
        ),
        "link": ParagraphStyle(
            "link", fontName=font, fontSize=9, leading=13,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2", fontName=font, fontSize=12, leading=18,
            spaceBefore=6, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body", fontName=font, fontSize=10, leading=15,
        ),
        "code": ParagraphStyle(
            "code", fontName=font, fontSize=9, leading=13,
            leftIndent=10, spaceBefore=2, spaceAfter=2,
            backColor="#f5f5f5",
        ),
    }

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        topMargin=16 * mm, bottomMargin=16 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = [
        Paragraph("CCPC 模拟训练卷 · 中文题面", styles["title"]),
        Paragraph(contest.get("contest_name", "模拟赛"), styles["h1"]),
        Spacer(1, 6),
    ]

    for p in contest.get("problems", []):
        letter = p.get("letter", "?")
        title = p.get("title", "")
        story.append(Paragraph(f"{letter}. {title}", styles["h1"]))
        url = p.get("url", "")
        if url:
            story.append(Paragraph(
                f'<link href="{url}"><font color="#1a5fb4"><u>{url}</u></font></link>',
                styles["link"],
            ))
        stmt = statements.get(letter, "")
        if stmt:
            _render_markdown(story, styles, stmt)
        else:
            story.append(Paragraph(
                "（题面未能自动抓取，请点击上方链接查看原题）",
                styles["body"],
            ))
        story.append(PageBreak())

    doc.build(story)
    return path


def _latex_to_text(s):
    """把残留的 LaTeX 命令转成可读文本（兜底，防止 AI 不按要求输出）。"""
    if not s:
        return s
    import re

    # 先处理带参数的命令
    s = re.sub(r"\\operatorname\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\text\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\texttt\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\mathit\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\textbf\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
    s = re.sub(r"\^\{([^{}]*)\}", r"^\1", s)
    s = re.sub(r"_\{([^{}]*)\}", r"_\1", s)
    # 简单命令映射
    for pat, rep in (
        (r"\\left", ""), (r"\\right", ""),
        (r"\\leq", "≤"), (r"\\le", "≤"),
        (r"\\geq", "≥"), (r"\\ge", "≥"),
        (r"\\neq", "≠"), (r"\\ne", "≠"),
        (r"\\times", "×"), (r"\\cdot", "·"),
        (r"\\ldots", "…"), (r"\\cdots", "…"), (r"\\dots", "…"),
        (r"\\sum", "Σ"), (r"\\prod", "∏"),
        (r"\\infty", "∞"), (r"\\pm", "±"),
        (r"\\to", "→"), (r"\\rightarrow", "→"), (r"\\leftarrow", "←"),
        (r"\\bmod", " mod "), (r"\\pmod", " mod "),
        (r"\\log", "log"), (r"\\ln", "ln"), (r"\\exp", "exp"),
        (r"\\gcd", "gcd"), (r"\\min", "min"), (r"\\max", "max"),
        (r"\\lfloor", "⌊"), (r"\\rfloor", "⌋"),
        (r"\\lceil", "⌈"), (r"\\rceil", "⌉"),
        (r"\\land", "∧"), (r"\\lor", "∨"),
        (r"\\in", "∈"), (r"\\cup", "∪"), (r"\\cap", "∩"),
    ):
        s = re.sub(pat, rep, s)
    # 其余反斜杠命令直接删除
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    # 去掉各种定界符
    s = s.replace("$", "").replace("\\(", "").replace("\\)", "")
    s = s.replace("\\[", "").replace("\\]", "")
    return s


def generate_round_pdf(contest, progress_cb=None):
    """抓取题面 → 翻译 → 生成 ccpc_roundX.pdf，返回 PDF 路径。

    progress_cb: 回调 (phase, done, total)，phase 为"抓取题面"或"翻译"。
    """
    problems = contest.get("problems", [])
    if not problems:
        raise ValueError("没有题目，无法生成题面")

    def cb(phase):
        return (lambda d, t: progress_cb(phase, d, t)) if progress_cb else None

    statements = _fetch_statements(problems, cb("抓取题面"))

    translated = {}
    for i, p in enumerate(problems, 1):
        letter = p.get("letter", "?")
        translated[letter] = _latex_to_text(_translate(statements.get(letter, "")))
        if progress_cb:
            progress_cb("翻译", i, len(problems))

    n = _next_round()
    path = os.path.join(PDF_DIR, f"ccpc_round{n}.pdf")
    build_pdf(contest, translated, path)
    return path
