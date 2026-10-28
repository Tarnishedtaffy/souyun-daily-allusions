"""搜韵典故索引抓取工具（本地运行，仅读取搜韵公开页面，不提交任何账号信息）

用法（也可直接双击 更新搜韵数据.bat / 补抓典籍原文.bat）：
    python 更新搜韵数据.py            # 抓取「典故数 >= 3」的人物（默认，覆盖列表靠前的人物）
    python 更新搜韵数据.py --fill     # 只补抓「缺典籍原文」的人物（增量补数据，推荐）
    python 更新搜韵数据.py --min 1    # 抓全部 2513 位（耗时很长，约 1 小时）
    python 更新搜韵数据.py --min 10   # 只抓典故数 >= 10 的人物（快）
    python 更新搜韵数据.py --people 3 # 只抓前 3 人（调试）
    python 更新搜韵数据.py --jobs 2   # 并发数（搜韵对并发很敏感，默认 1，不建议调大）

限流说明：搜韵对高频访问返回 429。脚本内置自适应间隔 + 指数退避 + 连续被限流时全局冷却，
        被限流时自动放慢而不是失败跳过。若长时间全是 429，说明该 IP 配额已用尽，
        过一段时间（或换个网络）再运行即可，已抓到的数据不会丢（断点续抓）。

产出（与 index.html 同目录，用 <script src> 本地加载，不走网络）：
    souyun-people.js     人物列表：严格按搜韵页面顺序，含每人典故总数
    souyun-allusions.js  人物名下典故明细：典故名 / 释义 / 出处（书名+卷次）/ 典籍原文 / 诗词例句

    明细字段：n=典故名  s=出处书名（含 ～卷次～ 标记，与搜韵页面一致）
              w=典籍原文（对应搜韵的"出处"原文段，超长截断）  d=简释  q=诗词例句

说明：
  · 搜韵人物页每页 15 条典故，人物索引括号内数字即该人物的典故条目总数，
    本脚本会翻页抓全，因此「本地条数」应与索引数字一致（个别条目若搜韵完全无内容会被跳过）。
  · 已抓过且非空的人物自动跳过（断点续抓）；每 25 人落盘一次，中断不丢进度。
"""
import os
import re
import ssl
import sys
import gzip
import json
import time
import random
import threading
import http.client
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

HOST = "sou-yun.cn"
BASE = "https://" + HOST
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HERE = os.path.dirname(os.path.abspath(__file__))
F_PEOPLE = os.path.join(HERE, "souyun-people.js")
F_ALLUS = os.path.join(HERE, "souyun-allusions.js")

DELAY_MIN = 3.0           # 每个线程的请求间隔下限（秒）
DELAY_MAX = 60.0          # 遇到限流后自动放大的上限
# 建议单线程运行（--jobs 1）：搜韵对并发很敏感，多线程会很快触发 429
DELAY = [DELAY_MIN]
MAX_Q = 2                 # 每条典故最多保留几条诗词例句
MAX_D = 110               # 释义最多保留字数
MAX_W = 180               # 典籍原文最多保留字数（超出截断加省略号，覆盖多数条目完整段落）
MAX_PAGES = 40            # 单个人物最多翻页数（防死循环）

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
_tls = threading.local()          # 每线程一条 keep-alive 连接
_print_lock = threading.Lock()
_cool = [0.0]                     # 全局冷却截止时间（连续被限流时启用）
_hits = [0]                       # 连续限流次数

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(*a):
    with _print_lock:
        print(*a)
        sys.stdout.flush()


def _fresh():
    old = getattr(_tls, "conn", None)
    try:
        if old:
            old.close()
    except Exception:
        pass
    c = http.client.HTTPSConnection(HOST, timeout=40, context=_ctx)
    _tls.conn = c
    _tls.last = 0.0
    return c


def get(path, retries=50):
    """keep-alive 连接 + 自适应间隔 + 限流退避（线程安全）"""
    for attempt in range(retries):
        # 全局冷却：连续被限流时整体停一会儿，避免把重试次数耗尽
        while True:
            left = _cool[0] - time.time()
            if left <= 0:
                break
            time.sleep(min(left, 5))
        wait = DELAY[0] - (time.time() - getattr(_tls, "last", 0.0))
        if wait > 0:
            time.sleep(wait)
        try:
            c = getattr(_tls, "conn", None) or _fresh()
            c.request("GET", path, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip",
                "Connection": "keep-alive",
                "Referer": BASE + "/AllusionsIndex.aspx",
            })
            r = c.getresponse()
            raw = r.read()
            status = r.status
            _tls.last = time.time()
            if status >= 400:
                _fresh()
                if status in (429, 500, 502, 503, 504):
                    if status == 429:
                        _hits[0] += 1
                        # 连续被限流：整体冷却一段时间再继续
                        if _hits[0] % 5 == 0:
                            cool = 180
                            _cool[0] = time.time() + cool
                            DELAY[0] = DELAY_MIN
                            log("    连续被限流 %d 次，全局冷却 %d 秒后继续"
                                % (_hits[0], cool))
                            time.sleep(cool)
                    DELAY[0] = min(DELAY_MAX, DELAY[0] * 1.8 + 0.2)
                    back = (attempt + 1) * 3 + random.random() * 2
                    log("    HTTP %d，间隔调到 %.2fs，%.1fs 后重试（%d/%d）"
                        % (status, DELAY[0], back, attempt + 1, retries))
                    time.sleep(back)
                    continue
                raise RuntimeError("HTTP %d: %s" % (status, path))
            if r.getheader("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            # 成功则缓慢回落间隔，并清空连续限流计数
            _hits[0] = 0
            DELAY[0] = max(DELAY_MIN, DELAY[0] * 0.96)
            return raw.decode("utf-8", "replace")
        except Exception:
            _tls.last = time.time()
            _fresh()
            if attempt == retries - 1:
                raise
            time.sleep((attempt + 1) * 2)
    raise RuntimeError("多次重试仍失败：" + path)


def clean(s):
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    s = re.sub(r"[ \t\u3000]+", " ", s)
    return "\n".join(x.strip() for x in s.split("\n") if x.strip()).strip()


# ---------------- 人物索引 ----------------
PERSON_RE = re.compile(
    r"<a href='/AllusionsIndex\.aspx\?people=(\d+)' class='expand'>([^<]+)"
    r"<span class='inlineComment2'>\s*\((\d+)\)</span></a>")
TOTAL_RE = re.compile(r"<option value='(\d+)'")


def fetch_people():
    people, page, npages = [], 0, None
    while True:
        html = get("/AllusionsIndex.aspx?sort=People&page=%d" % page)
        if npages is None:
            opts = TOTAL_RE.findall(html)
            npages = (max(int(x) for x in opts) + 1) if opts else 1
            log("  人物索引共 %d 页" % npages)
        found = PERSON_RE.findall(html)
        if not found:
            break
        for idx, name, cnt in found:
            people.append({"i": int(idx), "n": clean(name), "c": int(cnt)})
        log("  第 %d/%d 页，累计 %d 人" % (page + 1, npages, len(people)))
        page += 1
        if page >= npages:
            break
    people.sort(key=lambda p: p["i"])
    return people


# ---------------- 人物名下的典故（翻页抓全） ----------------
ITEM_SPLIT = re.compile(r"<div class='allusion' id='item_(\d+)'>")
NAME_RE = re.compile(r"<span class='indentLabel'>典故</span>\s*<div class='inline'>(.*?)</div>", re.S)
BOOK_RE = re.compile(r"<span class\s*='book'>(.*?)</span>", re.S)
# 典籍出处块：<div class='allusionBlock' id='allusion_content_0_531'> 书名 + 原文
CB_RE = re.compile(
    r"<div class='allusionBlock' id='allusion_content_\d+_\d+'>(.*?)</div>", re.S)
BC_RE = re.compile(r"<span\s*class='bookContent'>(.*?)</span>", re.S)
DEF_RE = re.compile(r"<span class='label'>简释</span></div>(.*?)(?=<div class='allusionBlock'>|\Z)", re.S)
QUO_RE = re.compile(r"<span class='label'>例句</span></div>(.*?)(?=<div class='allusionBlock'>|\Z)", re.S)
P_RE = re.compile(r"<p>(.*?)</p>", re.S)
CMT_RE = re.compile(r"<span class='inlineComment2'>(.*?)</span>", re.S)


def parse_items(html):
    i = html.find('id="ContentPanel"')
    body = html[i:] if i >= 0 else html
    parts = ITEM_SPLIT.split(body)
    out = {}
    for k in range(1, len(parts), 2):
        item_id, chunk = parts[k], parts[k + 1]
        m = NAME_RE.search(chunk)
        if not m:
            continue
        name = clean(m.group(1))
        if not name:
            continue
        md = DEF_RE.search(chunk)
        d = ""
        if md:
            ps = [clean(x) for x in P_RE.findall(md.group(1))]
            d = "；".join([x for x in ps if x][:2])
        if len(d) > MAX_D:
            d = d[:MAX_D] + "…"
        # 出处：优先取「典籍出处块」的书名（与搜韵页面显示一致，含 ～卷次～ 标记）
        src, word = "", ""
        blocks = CB_RE.findall(chunk)
        for b in blocks:
            mb = BOOK_RE.search(b)
            if mb and not src:
                src = re.sub(r"\s+", "", clean(mb.group(1)))
            mc = BC_RE.search(b)
            if mc and not word:
                word = clean(mc.group(1))
            if src and word:
                break
        if not src:
            mb = BOOK_RE.search(chunk)
            if mb:
                src = re.sub(r"\s+", "", clean(mb.group(1)))
        src = src[:60]
        if len(word) > MAX_W:
            word = word[:MAX_W] + "…"
        q = []
        mq = QUO_RE.search(chunk)
        if mq:
            for p in P_RE.findall(mq.group(1)):
                cm = CMT_RE.search(p)
                line = clean(CMT_RE.sub("", p))
                who = clean(cm.group(1)) if cm else ""
                if line:
                    q.append([line, who])
                if len(q) >= MAX_Q:
                    break
        # 无简释也无例句时仍保留（只展示典故名 + 出处 + 典籍原文），保证与搜韵条目数一致
        out[item_id] = {"n": name, "d": d, "s": src, "w": word, "q": q}
    return out


def fetch_person_allusions(idx):
    """翻页抓全某人物名下典故"""
    merged, page = {}, 0
    while page < MAX_PAGES:
        path = "/AllusionsIndex.aspx?people=%d" % idx + ("&page=%d" % page if page else "")
        html = get(path)
        got = parse_items(html)
        new = 0
        for k, v in got.items():
            if k not in merged:
                merged[k] = v
                new += 1
        nxt = "people=%d&page=%d" % (idx, page + 1)
        if not new or nxt not in html:
            break
        page += 1
    return [merged[k] for k in sorted(merged, key=lambda x: int(x))]


# ---------------- 读写 ----------------
def write_people(people, fetched_at):
    with open(F_PEOPLE, "w", encoding="utf-8") as f:
        f.write("/* 搜韵典故索引·人物列表（%s 抓取，仅含公开页面内容） */\n" % fetched_at)
        f.write("window.SOUYUN_PEOPLE=%s;\n"
                % json.dumps({"fetchedAt": fetched_at,
                              "source": BASE + "/AllusionsIndex.aspx?sort=People",
                              "people": people}, ensure_ascii=False, separators=(",", ":")))


def read_json(path, key):
    if not os.path.exists(path):
        return None
    s = open(path, encoding="utf-8").read()
    a, b = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[a:b + 1]).get(key)
    except Exception:
        return None


def write_allusions(allu, fetched_at):
    with open(F_ALLUS, "w", encoding="utf-8") as f:
        f.write("/* 搜韵典故明细（%s 抓取，仅含公开页面内容） */\n" % fetched_at)
        f.write("window.SOUYUN_ALLUSIONS=%s;\n"
                % json.dumps({"fetchedAt": fetched_at,
                              "source": BASE + "/AllusionsIndex.aspx?sort=People",
                              "allusions": allu}, ensure_ascii=False, separators=(",", ":")))


def main():
    args = sys.argv[1:]
    minc, only, jobs = 3, 0, 1
    if "--min" in args:
        minc = int(args[args.index("--min") + 1])
    if "--people" in args:
        only = int(args[args.index("--people") + 1])
    if "--jobs" in args:
        jobs = max(1, int(args[args.index("--jobs") + 1]))

    fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
    people = read_json(F_PEOPLE, "people") if os.path.exists(F_PEOPLE) else None
    if people:
        log("== 人物索引已存在，%d 人，跳过抓取 ==" % len(people))
    else:
        log("== 抓取人物索引 ==")
        people = fetch_people()
        log("人物总数：%d" % len(people))
        write_people(people, fetched_at)
        log("已写出 %s（%.1f KB）" % (os.path.basename(F_PEOPLE),
                                     os.path.getsize(F_PEOPLE) / 1024.0))

    targets = [p for p in people if p["c"] >= minc]
    if only:
        targets = targets[:only]
    log("前 6 位：", "  ".join("%s(%d)" % (p["n"], p["c"]) for p in people[:6]))

    allu = read_json(F_ALLUS, "allusions") or {}
    if "--fill" in args:
        # 只补「已有条目、但缺典籍原文(w)」的人物，用于增量补数据
        todo = [p for p in targets
                if allu.get(str(p["i"]))
                and any(not x.get("w") for x in allu[str(p["i"])])]
        log("== 补数据模式：%d 人缺典籍原文 ==" % len(todo))
    else:
        # 空数组视为「未抓到」，需要重抓
        todo = [p for p in targets if not allu.get(str(p["i"]))]
    done = len(targets) - len(todo)
    log("== 典故明细：目标 %d 人（典故数>=%d），已有 %d 人，本次待抓 %d 人 =="
        % (len(targets), minc, done, len(todo)))

    t0 = time.time()
    lock = threading.Lock()
    stat = [0]      # 已完成人数

    def work(p):
        try:
            items = fetch_person_allusions(p["i"])
        except Exception as e:
            log("  %s 失败：%s" % (p["n"], e))
            return
        key = str(p["i"])
        old = allu.get(key) or []
        # 保护：翻页中断会返回不完整结果，此时不要覆盖已有条目
        if old and len(items) < len(old):
            log("  %s 抓回 %d 条 < 已有 %d 条，判定不完整，跳过覆盖"
                % (p["n"], len(items), len(old)))
            return
        with lock:
            allu[key] = items
            stat[0] += 1
            k = stat[0]
            if k % 10 == 0 or k == len(todo):
                write_allusions(allu, fetched_at)
                cov = sum(1 for v in allu.values() if v)
                log("  [%d/%d] 落盘：%d 人 / %d 条 / %.1f KB / 间隔%.2fs / 用时 %.0fs"
                    % (k, len(todo), cov, sum(len(v) for v in allu.values()),
                       os.path.getsize(F_ALLUS) / 1024.0, DELAY[0], time.time() - t0))

    if jobs <= 1 or len(todo) <= 1:
        for p in todo:
            work(p)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            list(ex.map(work, todo))

    write_allusions(allu, fetched_at)
    cov = sum(1 for v in allu.values() if v)
    log("完成：%s（%.1f KB），覆盖 %d 人 / %d 条典故"
        % (os.path.basename(F_ALLUS), os.path.getsize(F_ALLUS) / 1024.0,
           cov, sum(len(v) for v in allu.values())))


if __name__ == "__main__":
    main()
