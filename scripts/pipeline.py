#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流水线：链接 → 音频下载 + 互动数据抓取 + 逐字稿转写。

用法:
  python pipeline.py "<小红书链接>" [更多链接...]
  python pipeline.py --from-group          # 从飞书群拉新链接
  python pipeline.py "<链接>" --model large-v3-turbo

幂等：已下载/已转写/已在台账的自动跳过。
结束输出 ===PIPELINE_RESULT=== JSON 块（供 agent 写分析用）。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
from config_lib import load_config
CFG = load_config()
TZ = timezone(timedelta(hours=8))
LARK = shutil.which("lark-cli")

RESULTS = []


def log(msg):
    print(msg, flush=True)


def load_library(workdir: Path) -> dict:
    f = workdir / "library.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"videos": {}}


def save_library(workdir: Path, lib: dict):
    (workdir / "library.json").write_text(
        json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")


def note_id(url: str) -> str:
    m = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{24})", url)
    return m.group(1) if m else ""


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def build_opener():
    """国内默认直连小红书；海外用户在 config 设 "direct_connect": false 走代理。"""
    if CFG.get("direct_connect", True):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    proxy = CFG.get("proxy") or ""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def resolve_url(url: str) -> str:
    """短链（xhslink.cn 等）跟随 302 跳转解析成完整笔记链接。"""
    if "xiaohongshu.com" in url:
        return url
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://www.xiaohongshu.com/"})
    final = build_opener().open(req, timeout=30).geturl()
    if "xiaohongshu.com" not in final:
        raise ValueError(f"短链没有跳转到小红书: {final}")
    return final


def check_url(url: str):
    if "xiaohongshu.com" not in url:
        raise ValueError(f"不是小红书链接: {url}")
    if "xsec_token" not in url:
        raise ValueError(
            "链接缺少 xsec_token 参数（裸链接拿不到任何数据）。"
            "请让用户在浏览器打开视频后全选地址栏复制完整链接，或点「分享→复制链接」")


def dl_audio(url: str, workdir: Path):
    """yt-dlp 下载音频（m4a），返回 (路径, 标题, 时长秒)。已存在则跳过下载但取元数据。"""
    import yt_dlp

    dl_dir = workdir / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    nid = note_id(url)
    final = dl_dir / f"{nid}.m4a"

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dl_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
    }
    if CFG.get("ffmpeg_location") and Path(CFG["ffmpeg_location"]).exists():
        opts["ffmpeg_location"] = CFG["ffmpeg_location"]
    opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=not final.exists())
        title = info.get("title") or nid
        duration = int(round(info.get("duration") or 0))

    if not final.exists():
        # postprocessor 后缀可能是 m4a 以外的中间名，兜底找一下
        hits = list(dl_dir.glob(f"{nid}.*"))
        hits = [h for h in hits if h.suffix in (".m4a", ".mp3", ".aac", ".wav")]
        if not hits:
            raise RuntimeError(f"下载后找不到音频文件: {nid}")
        final = hits[0]
    return final, title, duration


CN_NUM = {"万": 10000, "w": 10000, "亿": 100000000}

def parse_count(s: str) -> int:
    """'1.3万' → 13000；'8148' → 8148；空/异常 → 0"""
    s = (s or "").strip()
    if not s:
        return 0
    m = re.match(r"^([\d.]+)([万w亿]?)$", s)
    if not m:
        return 0
    num = float(m.group(1))
    mult = CN_NUM.get(m.group(2), 1)
    return int(round(num * mult))


def fetch_meta(url: str) -> dict:
    """抓笔记页，解析互动数据/昵称/发布时间。默认直连（海外用户可在 config 关闭 direct_connect）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.xiaohongshu.com/",
    })
    html = build_opener().open(req, timeout=40).read().decode("utf-8", errors="replace")

    meta = {"likes": 0, "comments": 0, "collects": 0, "shares": 0,
            "blogger": "", "publishedAt": ""}
    m = re.search(r'"interactInfo":\{[^}]*\}', html)
    if m:
        block = m.group(0)
        for key, field in [("likedCount", "likes"), ("commentCount", "comments"),
                           ("collectedCount", "collects"), ("shareCount", "shares")]:
            v = re.search(rf'"{key}":"([^"]*)"', block)
            meta[field] = parse_count(v.group(1)) if v else 0
    else:
        log(f"  [!] 页面里没有 interactInfo——多半是链接缺 xsec_token 或已失效")
    m = re.search(r'"nickname":"([^"]*)"', html)
    if m:
        meta["blogger"] = m.group(1)
    m = re.search(r'"time":(1[0-9]{12})', html)
    if m:
        meta["publishedAt"] = datetime.fromtimestamp(int(m.group(1)) / 1000, TZ).strftime("%Y-%m-%d")
    return meta


def resolve_model_path(model_name: str, workdir: Path):
    """在多个已知位置查找已下载的模型快照；找到则返回可直接加载的路径，否则 None。

    覆盖：素材库 models/、系统默认 HF 缓存（其他 agent/工具的默认下载位置）、config 指定目录。
    """
    candidates = [workdir / "models",
                  Path.home() / ".cache" / "huggingface" / "hub"]
    if CFG.get("model_dir"):
        candidates.insert(0, Path(CFG["model_dir"]))
    for base in candidates:
        if not base.is_dir():
            continue
        for mdir in base.glob(f"models--*faster-whisper-{model_name}"):
            for snap in sorted((mdir / "snapshots").glob("*")):
                if (snap / "model.bin").exists():
                    return str(snap)
    return None


def fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(audio: Path, workdir: Path, model_name: str, device: str,
               compute_type: str, language: str | None, proxy: str):
    """faster-whisper 转写，输出与既有格式一致的 txt/srt/json。幂等。"""
    tr_dir = workdir / "transcripts"
    tr_dir.mkdir(parents=True, exist_ok=True)
    out_json = tr_dir / f"{audio.stem}.json"
    if out_json.exists():
        log(f"  [跳过] 逐字稿已存在: {out_json.name}")
        return out_json

    import os
    from faster_whisper import WhisperModel
    t0 = time.time()
    existing = resolve_model_path(model_name, workdir)
    if existing:
        log(f"  [转写] 复用已有模型: {existing}")
        model = WhisperModel(existing, device=device, compute_type=compute_type)
    else:
        if proxy:
            os.environ["http_proxy"] = proxy
            os.environ["https_proxy"] = proxy
        log(f"  [转写] 未找到本地模型，开始下载 {model_name}（约3GB，走代理，只需一次）")
        log(f"  [转写] 模型={model_name} 设备={device}/{compute_type}（CPU 约为音频时长的 2 倍时间）")
        model = WhisperModel(model_name, device=device, compute_type=compute_type,
                             download_root=str(workdir / "models"))
    segments, info = model.transcribe(str(audio), language=language or None,
                                      beam_size=5, vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": 500})
    seg_list = []
    for seg in segments:
        seg_list.append({"start": round(seg.start, 3), "end": round(seg.end, 3),
                         "text": seg.text.strip()})
    result = {"language": info.language, "language_probability": round(info.language_probability, 2),
              "duration": info.duration, "segments": seg_list}
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (tr_dir / f"{audio.stem}.txt").write_text(
        "\n\n".join(s["text"] for s in seg_list if s["text"]), encoding="utf-8")
    srt = "\n".join(
        f"{i}\n{fmt_ts(s['start'])} --> {fmt_ts(s['end'])}\n{s['text']}\n"
        for i, s in enumerate((x for x in seg_list if x["text"]), 1))
    (tr_dir / f"{audio.stem}.srt").write_text(srt, encoding="utf-8")
    log(f"  [完成] 转写耗时 {time.time()-t0:.0f}s，共 {len(seg_list)} 段")
    return out_json


def process_url(url: str, workdir: Path, args) -> dict:
    url = resolve_url(url.strip())
    nid = note_id(url)
    lib = load_library(workdir)
    rec = lib["videos"].get(nid, {})

    if rec.get("status", {}).get("transcribed"):
        log(f"[跳过] {nid} 已处理过（library.json 有记录）")
        return {"id": nid, "status": "already_done", **{k: rec.get(k) for k in
                ("title", "duration", "blogger", "likes", "comments", "collects", "shares")}}

    check_url(url)
    log(f"[0/3] 完整链接: {url[:100]}…")
    log(f"[1/3] 下载音频: {nid}")
    audio, title, duration = dl_audio(url, workdir)
    log(f"  [完成] {audio.name}（{duration}s）标题: {title}")

    log("[2/3] 抓互动数据（点赞/评论/收藏/分享/博主/发布日期）")
    meta = fetch_meta(url)

    log("[3/3] 转写逐字稿")
    tr = transcribe(audio, workdir, args.model or CFG["model"], CFG.get("device", "cpu"),
                    CFG.get("compute_type", "int8"), args.language or CFG.get("language"),
                    CFG.get("proxy"))

    rec.update({
        "title": title, "duration": duration, "url": url,
        "publishedAt": meta["publishedAt"], "collectedAt": datetime.now(TZ).strftime("%Y-%m-%d"),
        "blogger": meta["blogger"],
        "likes": meta["likes"], "comments": meta["comments"],
        "collects": meta["collects"], "shares": meta["shares"],
        "audio": str(audio), "transcript": str(tr),
    })
    tr_data = json.loads(tr.read_text(encoding="utf-8"))
    lang = tr_data.get("language", "")
    needs_zh = bool(lang) and lang not in ("zh", "yue")
    if needs_zh:
        log(f"  [提示] 检测到非中文视频（{lang}）：请在分析阶段生成中文对照翻译 "
            f"transcripts/{nid}.zh.json（格式见 analysis-rubric.md）")
    rec["status"] = {"downloaded": True, "transcribed": True,
                     "analyzed": (workdir / "analyses" / f"{nid}.json").exists(),
                     "published": False}
    lib["videos"][nid] = rec
    save_library(workdir, lib)

    return {"id": nid, "status": "ok", "title": title, "duration": duration,
            "blogger": meta["blogger"], "publishedAt": meta["publishedAt"],
            "collectedAt": rec["collectedAt"],
            "language": lang, "needs_zh_translation": needs_zh,
            "likes": meta["likes"], "comments": meta["comments"],
            "collects": meta["collects"], "shares": meta["shares"],
            "audio": str(audio), "transcript": str(tr),
            "transcript_text": (workdir / "transcripts" / f"{nid}.txt").read_text(encoding="utf-8")}


def urls_from_group() -> list[str]:
    chat_id = CFG.get("group_chat_id")
    if not chat_id:
        raise SystemExit("config.json 未配置 group_chat_id")
    if not LARK:
        raise SystemExit("找不到 lark-cli（npm 全局包）")
    import os
    env = {**os.environ, "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
           "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}
    out = subprocess.run([LARK, "im", "+chat-messages-list", "--chat-id", chat_id, "--json"],
                         capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    msgs = json.loads(out.stdout)["data"]["messages"]
    urls = []
    for m in msgs:
        urls += re.findall(r"https://www\.xiaohongshu\.com/[^\)\s]+", m.get("content", ""))
    seen, uniq = set(), []
    for u in urls:
        if "&" in u:
            u = u.replace("&amp;", "&")
        if note_id(u) and note_id(u) not in seen:
            seen.add(note_id(u))
            uniq.append(u)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="小红书链接（带 xsec_token）")
    ap.add_argument("--from-group", action="store_true", help="从飞书群拉新链接")
    ap.add_argument("--model"), ap.add_argument("--language")
    ap.add_argument("--workdir", default=CFG.get("workdir"))
    args = ap.parse_args()

    if not args.workdir:
        raise SystemExit("config.json 未配置 workdir，且未传 --workdir")
    workdir = Path(args.workdir)
    for d in ("downloads", "transcripts", "analyses"):
        (workdir / d).mkdir(parents=True, exist_ok=True)

    urls = list(args.urls)
    if args.from_group:
        group_urls = urls_from_group()
        lib = load_library(workdir)
        new = [u for u in group_urls if not lib["videos"].get(note_id(u), {}).get("status", {}).get("transcribed")]
        log(f"[群] 共 {len(group_urls)} 条链接，其中 {len(new)} 条未处理")
        urls += new
    if not urls:
        raise SystemExit("没有待处理的链接")

    for url in urls:
        log("=" * 60)
        try:
            RESULTS.append(process_url(url, workdir, args))
        except Exception as e:
            log(f"[失败] {type(e).__name__}: {e}")
            RESULTS.append({"status": "error", "url": url, "error": str(e)})

    log("\n===PIPELINE_RESULT===")
    log(json.dumps({"results": RESULTS, "workdir": str(workdir)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
