#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 analyses/*.json 写入飞书多维表格。

自动完成：查重（按笔记 id）→ select 选项自动补齐 → 批量写入 → 更新 library.json 状态。
config.json 缺 base_token 时自动新建 Base 并回存。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LARK = shutil.which("lark-cli")
if not LARK:
    raise SystemExit("找不到 lark-cli（npm 全局包）。请先 npm install -g 安装并确认在 PATH 中")

SKILL_DIR = Path(__file__).resolve().parent.parent
from config_lib import load_config, save_config, CONFIG_PATH
CFG = load_config()

FIELDS_SCHEMA = [
    {"type": "text", "name": "标题"},
    {"type": "select", "name": "博主"},
    {"type": "text", "name": "逐字稿"},
    {"type": "text", "name": "链接", "style": {"type": "url"}},
    {"type": "select", "name": "领域"},
    {"type": "select", "name": "钩子类型"},
    {"type": "datetime", "name": "发布日期", "style": {"format": "yyyy-MM-dd"}},
    {"type": "datetime", "name": "采集日期", "style": {"format": "yyyy-MM-dd"}},
    {"type": "number", "name": "时长秒", "style": {"type": "plain", "precision": 0}},
    {"type": "number", "name": "点赞", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "评论", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "收藏", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "分享", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "收藏赞比", "style": {"type": "plain", "precision": 2}},
    {"type": "text", "name": "优势"},
    {"type": "text", "name": "不足"},
    {"type": "text", "name": "可复用话术"},
]
SELECT_FIELDS = ["博主", "领域", "钩子类型"]


def lark(args: list, timeout: int = 90, cwd=None) -> dict:
    env = {**os.environ, "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
           "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}
    p = subprocess.run([LARK, *args, "--as", "user"], capture_output=True,
                       text=True, encoding="utf-8", env=env, timeout=timeout, cwd=cwd)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"message": (p.stdout + p.stderr)[:500]}}


def ensure_base(cfg: dict) -> tuple[str, str]:
    """返回 (base_token, table_id)；缺则自动创建。"""
    if cfg.get("base_token") and cfg.get("table_id"):
        return cfg["base_token"], cfg["table_id"]
    print("[Base] 未配置，正在自动创建「视频拆解工作台」…")
    r = lark(["base", "+base-create", "--name", "视频拆解工作台",
              "--table-name", "拆解记录",
              "--fields", json.dumps(FIELDS_SCHEMA, ensure_ascii=False)])
    if not r.get("ok"):
        raise SystemExit(f"创建 Base 失败: {json.dumps(r.get('error'), ensure_ascii=False)}")
    token = r["data"]["base"]["base_token"]
    tl = lark(["base", "+table-list", "--base-token", token])
    table_id = tl["data"]["tables"][0]["id"]
    cfg["base_token"], cfg["table_id"] = token, table_id
    save_config(cfg)
    print(f"[Base] 已创建并回存 config: {r['data']['base']['url']}")
    return token, table_id


def field_options(token: str, table_id: str, field_name: str) -> list[str]:
    r = lark(["base", "+field-list", "--base-token", token, "--table-id", table_id])
    if not r.get("ok"):
        return []
    for f in r["data"]["fields"]:
        if f["name"] == field_name:
            return [o["name"] for o in f.get("options", [])]
    return []


def ensure_options(token: str, table_id: str, needed: dict[str, set]):
    for fname, values in needed.items():
        existing = field_options(token, table_id, fname)
        missing = sorted(v for v in values if v and v not in existing)
        if not missing:
            continue
        print(f"[选项] {fname} 缺 {missing}，自动补齐…")
        merged = [{"name": v} for v in existing + missing]
        r = lark(["base", "+field-update", "--base-token", token, "--table-id", table_id,
                  "--field-id", fname, "--yes",
                  "--json", json.dumps({"type": "select", "name": fname,
                                        "multiple": False, "options": merged}, ensure_ascii=False)])
        if not r.get("ok"):
            raise SystemExit(f"补选项失败 {fname}: {json.dumps(r.get('error'), ensure_ascii=False)}")


def existing_ids(token: str, table_id: str) -> set[str]:
    """从 record-list 的 markdown 输出里抽笔记 id。

    注意：+record-list 不支持 --json（会报 unknown flag），只输出 markdown 表。
    解析失败返回空集，调用方必须配合 records_count 做防重复写入检查。
    """
    env = {**os.environ, "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
           "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}
    p = subprocess.run(
        [LARK, "base", "+record-list", "--base-token", token,
         "--table-id", table_id, "--limit", "200", "--as", "user"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    return set(re.findall(r"/(?:explore|discovery/item)/([0-9a-f]{24})",
                          (p.stdout or "") + (p.stderr or "")))


def table_record_count(token: str) -> int:
    r = lark(["base", "+table-list", "--base-token", token])
    if not r.get("ok"):
        return -1
    for t in r["data"]["tables"]:
        if t["id"] == CFG.get("table_id"):
            return t.get("records_count", 0)
    return -1


def row_from_analysis(a: dict, transcript_txt: str) -> list:
    ratio = round(a["collects"] / a["likes"], 2) if a["likes"] else 0
    return [
        a["title"], a["blogger"], transcript_txt, a["url"], a["field"], a["hookType"],
        a["publishedAt"] + " 00:00:00", a["collectedAt"] + " 00:00:00",
        a["duration"], a["likes"], a["comments"], a["collects"], a["shares"], ratio,
        "\n".join(f"{i}. {s}" for i, s in enumerate(a["strengths"], 1)),
        "\n".join(f"{i}. {s}" for i, s in enumerate(a["weaknesses"], 1)),
        "\n".join(f"「{s['quote']}」适用：{s['scene']}" for s in a["scripts"]),
    ]


def main():
    workdir = Path(CFG["workdir"])
    analyses_dir = workdir / "analyses"
    if not analyses_dir.is_dir():
        raise SystemExit(f"找不到 {analyses_dir}")

    pending = []
    for f in sorted(analyses_dir.glob("*.json")):
        a = json.loads(f.read_text(encoding="utf-8"))
        tr_txt = workdir / "transcripts" / f"{a['id']}.txt"
        if not tr_txt.exists():
            print(f"[跳过] {a['id']} 缺逐字稿文本")
            continue
        pending.append((a, tr_txt))
    if not pending:
        raise SystemExit("没有可入库的分析")

    token, table_id = ensure_base(CFG)
    done = existing_ids(token, table_id)
    total = table_record_count(token)
    if not done and total > 0:
        raise SystemExit(
            f"[中止] 表里有 {total} 条记录但无法解析出已有笔记 id，"
            "为避免重复写入已停止。请人工检查 +record-list 输出格式后重试。")
    rows, cols = [], ["标题", "博主", "逐字稿", "链接", "领域", "钩子类型", "发布日期", "采集日期",
                      "时长秒", "点赞", "评论", "收藏", "分享", "收藏赞比",
                      "优势", "不足", "可复用话术"]
    for a, tr_txt in pending:
        if a["id"] in done:
            print(f"[跳过] {a['id']} 已在 Base 中")
            continue
        rows.append(row_from_analysis(a, "\n".join(
            x.strip() for x in tr_txt.read_text(encoding="utf-8").split("\n\n") if x.strip())))

    if not rows:
        print("[完成] 没有新记录需要写入")
        return

    ensure_options(token, table_id, {
        "博主": {a["blogger"] for a, _ in pending},
        "领域": {a["field"] for a, _ in pending},
        "钩子类型": {a["hookType"] for a, _ in pending},
    })

    payload = {"fields": cols, "rows": rows}
    # @file 只接受相对路径：把 payload 写进独立临时目录，lark-cli 以该目录为 cwd 执行
    tmpdir = Path(tempfile.mkdtemp(prefix="xhs_lab_"))
    (tmpdir / "batch.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        r = lark(["base", "+record-batch-create", "--base-token", token,
                  "--table-id", table_id, "--json", "@batch.json"], cwd=tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if not r.get("ok"):
        raise SystemExit(f"写入失败: {json.dumps(r.get('error'), ensure_ascii=False)}")

    n = len(r["data"].get("record_id_list", []))
    print(f"[完成] 已写入 {n} 条记录 → https://my.feishu.cn/base/{token}")

    lib_f = workdir / "library.json"
    if lib_f.exists():
        lib = json.loads(lib_f.read_text(encoding="utf-8"))
        for a, _ in pending:
            if lib["videos"].get(a["id"]):
                lib["videos"][a["id"]]["status"]["published"] = True
        lib_f.write_text(json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
