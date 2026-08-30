#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：python/yt-dlp/ffmpeg/faster-whisper/模型/代理/config。--fix 自动修补 config。"""
import json
import os
import shutil
import socket
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"
DEFAULTS = {
    "workdir": "",
    "base_token": "",
    "table_id": "",
    "group_chat_id": "",
    "proxy": "http://127.0.0.1:7897",
    "model": "large-v3",
    "device": "cpu",
    "compute_type": "int8",
    "language": "zh",
    "ffmpeg_location": "",
}

OK, BAD, WARN = "✓", "✗", "!"
results = []

def item(ok, name, note=""):
    mark = OK if ok else BAD
    results.append(f"  {mark} {name}" + (f"（{note}）" if note else ""))
    return ok

def find_ffmpeg():
    hit = shutil.which("ffmpeg")
    if hit:
        return hit
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    if base.exists():
        for pkg in base.iterdir():
            if "ffmpeg" in pkg.name.lower():
                for exe in pkg.rglob("ffmpeg.exe"):
                    return str(exe)
    return ""

def proxy_alive(proxy):
    if not proxy:
        return False
    try:
        host, port = proxy.split("//")[1].split(":")
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except Exception:
        return False

def main():
    fix = "--fix" in sys.argv
    print("[环境自检]", flush=True)

    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update({k: v for k, v in json.loads(CONFIG_PATH.read_text(encoding="utf-8")).items() if v})
    else:
        print(f"  {WARN} 未发现 config.json，将以默认值创建（--fix 写盘）", flush=True)

    item(sys.version_info >= (3, 8), f"Python {sys.version.split()[0]}")

    try:
        import yt_dlp  # noqa
        item(True, "yt-dlp", getattr(yt_dlp, "version", None) and yt_dlp.version.__version__)
    except ImportError:
        item(False, "yt-dlp 未安装", "修复: pip install yt-dlp -i https://pypi.tuna.tsinghua.edu.cn/simple")

    ffmpeg = cfg.get("ffmpeg_location") or find_ffmpeg()
    if ffmpeg and Path(ffmpeg).exists():
        item(True, "ffmpeg", ffmpeg)
    else:
        item(False, "ffmpeg 未找到", "修复: winget install Gyan.FFmpeg（--force 补 PATH）")

    try:
        import faster_whisper  # noqa
        item(True, "faster-whisper")
    except ImportError:
        item(False, "faster-whisper 未安装", "修复: pip install faster-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple")

    workdir = Path(cfg.get("workdir") or "")
    if workdir.is_dir():
        model_dir = workdir / "models"
        has_model = model_dir.is_dir() and any(model_dir.glob("models--*"))
        item(has_model, "转写模型缓存", "models/ 已有模型" if has_model else "首次转写会自动下载（需代理，约30分钟）")
    else:
        item(False, "工作目录不存在", cfg.get("workdir") or "config.json 未配置 workdir")

    px = cfg.get("proxy", "")
    if proxy_alive(px):
        item(True, "代理可用", px)
    else:
        results.append(f"  {WARN} 代理不在线（仅影响模型下载/GitHub；抓小红书直连不受影响）")

    has_base = bool(cfg.get("base_token")) and bool(cfg.get("table_id"))
    item(has_base, "飞书 Base 配置", cfg.get("base_token", "")[:12] or "首次入库时自动创建")

    if fix:
        cfg["ffmpeg_location"] = ffmpeg
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {OK} config.json 已写入 {CONFIG_PATH}", flush=True)

    print("\n".join(results), flush=True)
    hard_fail = any(r.startswith(f"  {BAD}") for r in results)
    sys.exit(1 if hard_fail else 0)

if __name__ == "__main__":
    main()
