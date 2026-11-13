# -*- coding: utf-8 -*-
"""从官方题解（Tutorial）提取题目标签，写入 memory/problem_bank.json。

思路：每题解都写明了用的算法，比从题面猜测可靠得多。
流程：
  1. 打开每场 gym 主页，找 "Tutorial" 链接
  2. CF 附件（PDF/PPTX）自动下载并用 pypdf / zipfile 提取文本；
     Dropbox 链接自动转直链下载；知乎/网盘/视频等来源会列出，需手动处理
  3. 把题解全文 + 题目列表交给 AI，按题提取知识点标签

用法：
  python fetch_tutorial_tags.py                 # 处理所有比赛
  python fetch_tutorial_tags.py --gyms 104065   # 只处理指定比赛
  python fetch_tutorial_tags.py --force         # 已有标签也重新提取
"""

import asyncio
import json
import os
import re
import sys
import time
import zipfile

import requests

import config
from AI_agent import ask_gpt

TUTORIAL_DIR = os.path.join(os.path.dirname(config.BANK_FILE), "tutorials")
_PROFILE_DIR = os.path.join(config._BASE_DIR, "memory", "edge_profile")
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

_PROMPT = """下面是一场 CCPC 区域赛的官方题解，以及该场比赛的题目列表。
请依据题解，为每道题提取知识点/算法标签。

题目列表：
{problems}

题解全文：
{tutorial}

要求：
1. 只依据题解内容判断；题解中没讲到的题写空数组。
2. 标签用中文常用说法，例如：模拟、贪心、二分答案、动态规划、背包、图论、最短路、
   最小生成树、树、线段树、树状数组、并查集、字符串哈希、KMP、后缀数组、数论、
   组合数学、概率期望、计算几何、构造、搜索、位运算、矩阵快速幂、网络流、枚举。
3. 每题最多 4 个标签，按主要程度排序。
4. 只输出 JSON 对象，不要其他文字：{{"A": ["概率期望"], "B": ["贪心", "枚举"]}}
"""


def load_bank():
    with open(config.BANK_FILE, "r", encoding="utf8") as f:
        return json.load(f)


def save_bank(data):
    with open(config.BANK_FILE, "w", encoding="utf8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_text(path):
    """PDF / PPTX / TXT → 纯文本。"""
    lower = path.lower()
    if lower.endswith(".txt"):
        with open(path, "r", encoding="utf8", errors="ignore") as f:
            return f.read()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = []
        for pg in reader.pages:
            try:
                pages.append(pg.extract_text() or "")
            except Exception:  # noqa: BLE001
                pages.append("")
        return "\n".join(pages)
    if lower.endswith(".pptx"):
        parts = []
        with zipfile.ZipFile(path) as z:
            names = [
                n for n in z.namelist()
                if re.match(r"ppt/slides/slide\d+\.xml$", n)
            ]
            names.sort(key=lambda s: int(re.search(r"(\d+)", s.split("/")[-1]).group(1)))
            for n in names:
                xml = z.read(n).decode("utf8", "ignore")
                parts.append(" ".join(re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)))
        return "\n".join(parts)
    return ""


async def _fetch_zhihu_text(url):
    """用浏览器打开知乎页面，提取回答/文章正文。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            _PROFILE_DIR, channel="msedge", headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
            text = await page.eval_on_selector_all(
                "article, .RichContent-inner, .RichText, .QuestionAnswer-content",
                "els => els.map(e => e.innerText).join('\\n')"
            )
            return (text or "").strip()
        except Exception:  # noqa: BLE001
            return ""
        finally:
            await ctx.close()


async def _fetch_links(gyms):
    """打开每场 gym 主页，返回 {gym_id: 题解链接}。"""
    from playwright.async_api import async_playwright

    out = {}
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            _PROFILE_DIR, channel="msedge", headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = await ctx.new_page()
            for gid in gyms:
                try:
                    await page.goto(
                        f"https://codeforces.com/gym/{gid}",
                        wait_until="domcontentloaded", timeout=25000,
                    )
                    for _ in range(5):
                        if "Just a moment" not in (await page.title() or ""):
                            break
                        await page.wait_for_timeout(2000)
                    links = await page.eval_on_selector_all(
                        "a", "els => els.map(e => [e.innerText.trim(), e.href])"
                    )
                    hits = [h for t, h in links if "tutorial" in (t or "").lower()]
                    if hits:
                        out[gid] = hits[0]
                except Exception as e:  # noqa: BLE001
                    print(f"[警告] gym {gid} 主页打开失败：{e}")
        finally:
            await ctx.close()
    return out


def _local_tutorial(gid, url):
    """下载/复用题解文件，返回本地路径；不支持的来源返回 None。"""
    from urllib.parse import unquote

    os.makedirs(TUTORIAL_DIR, exist_ok=True)

    # 优先使用本地已有的题解文件（文件名以 gym_id_ 开头，如 102798_weihai.pdf）
    for fn in os.listdir(TUTORIAL_DIR):
        if fn.startswith(f"{gid}_"):
            fp = os.path.join(TUTORIAL_DIR, fn)
            if os.path.isfile(fp) and os.path.getsize(fp) > 1024:
                return fp

    if ("codeforces.com" in url and "/attachments/" in url) or "xcpcio.com" in url:
        name = unquote(os.path.basename(url.split("?")[0]))
        dest = os.path.join(TUTORIAL_DIR, f"{gid}_{name}")
        direct = url
    elif "dropbox.com" in url:
        direct = re.sub(r"[?&]dl=0", "", url)
        direct += ("&" if "?" in direct else "?") + "dl=1"
        dest = os.path.join(TUTORIAL_DIR, f"{gid}_tutorial.pdf")
    elif "drive.google.com" in url:
        m = re.search(r"/d/([\w-]+)", url) or re.search(r"id=([\w-]+)", url)
        if not m:
            return None
        dest = os.path.join(TUTORIAL_DIR, f"{gid}_gdrive.pdf")
        if os.path.exists(dest) and os.path.getsize(dest) > 1024:
            return dest
        try:
            import gdown

            out = gdown.download(f"https://drive.google.com/uc?id={m.group(1)}", dest, quiet=True)
            if out and os.path.getsize(out) > 1024:
                print(f"  已下载题解（GDrive）：{os.path.basename(dest)}（{os.path.getsize(dest)//1024} KB）")
                return dest
        except Exception as e:  # noqa: BLE001
            print(f"  GDrive 下载失败：{e}")
        return None
    elif "zhihu.com" in url:
        dest = os.path.join(TUTORIAL_DIR, f"{gid}_zhihu.txt")
        if os.path.exists(dest) and os.path.getsize(dest) > 500:
            return dest
        text = asyncio.run(_fetch_zhihu_text(url))
        if text and len(text) > 300:
            with open(dest, "w", encoding="utf8") as f:
                f.write(text)
            print(f"  已抓取知乎题解：{os.path.basename(dest)}（{len(text)} 字）")
            return dest
        print("  知乎正文抓取失败（可能需要登录），跳过")
        return None
    else:
        return None  # Bilibili 视频等需手动处理

    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        return dest
    try:
        r = requests.get(direct, headers=_UA, timeout=120)
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        print(f"  已下载题解：{os.path.basename(dest)}（{len(r.content)//1024} KB）")
        return dest
    except Exception as e:  # noqa: BLE001
        print(f"  下载失败：{e}")
        return None


def ai_extract_tags(tutorial_text, probs):
    """按题从题解中提取标签，返回 {letter: [tags]}。"""
    lines = "\n".join(f'{p.get("letter")}. {p.get("title")}' for p in probs)
    try:
        result = ask_gpt(
            _PROMPT.format(problems=lines, tutorial=tutorial_text[:40000]),
            system="你是算法竞赛教练，只输出严格的 JSON。",
        )
    except Exception as e:  # noqa: BLE001
        print(f"  AI 提取失败：{e}")
        return {}
    if not isinstance(result, dict):
        return {}
    out = {}
    for letter, tags in result.items():
        if isinstance(tags, list):
            clean = []
            for t in tags:
                t = str(t).strip()
                if t and t not in clean:
                    clean.append(t)
            if clean:
                out[str(letter).strip()] = clean[:4]
    return out


def main(only_gyms=None, force=False):
    data = load_bank()
    problems = data.get("problems", []) if isinstance(data, dict) else data
    if not problems:
        print("题库为空，请先运行 build_problem_bank.py")
        return

    gyms = sorted({p.get("gym_id") for p in problems if p.get("gym_id")})
    if only_gyms:
        gyms = [g for g in gyms if g in only_gyms]

    print(f"查找 {len(gyms)} 场比赛的题解链接……")
    links = asyncio.run(_fetch_links(gyms))

    manual = []
    updated_total = 0
    for gid in gyms:
        url = links.get(gid)
        if not url:
            print(f"[跳过] gym {gid}: 未找到题解链接")
            continue
        probs = [p for p in problems if p.get("gym_id") == gid]
        if not force and all(p.get("tag_source") == "tutorial" for p in probs):
            print(f"[跳过] gym {gid}: 已有题解标签")
            continue

        print(f"[处理] gym {gid}")
        print(f"  题解：{url[:110]}")
        path = _local_tutorial(gid, url)
        if not path:
            print("  该来源需手动下载（放可放过），已记录")
            manual.append((gid, url))
            continue

        text = extract_text(path)
        if len(text.strip()) < 200:
            print("  题解文本过短（可能是图片型），跳过")
            continue

        tags_map = ai_extract_tags(text, probs)
        if not tags_map:
            print("  未能提取到标签")
            continue

        for p in probs:
            tags = tags_map.get(p.get("letter"))
            if tags:
                p["tags"] = tags
                p["tag_source"] = "tutorial"
                updated_total += 1
        print(f"  已为 {sum(1 for p in probs if tags_map.get(p.get('letter')))} 道题写入标签")
        data["tags_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_bank(data)

    save_bank(data)
    print(f"\n完成：共更新 {updated_total} 道题的标签，写入 {config.BANK_FILE}")
    if manual:
        print("\n以下比赛题解来源需手动处理：")
        for gid, url in manual:
            print(f"  gym {gid}: {url}")


if __name__ == "__main__":
    args = sys.argv[1:]
    only = None
    if "--gyms" in args:
        i = args.index("--gyms")
        try:
            only = [int(x) for x in args[i + 1].split(",")]
        except (ValueError, IndexError):
            print("用法: python fetch_tutorial_tags.py [--force] [--gyms 104065]")
            sys.exit(1)
    main(only, "--force" in args)
