#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并 analyses/*.json + transcripts/ + insights.json → 工作台 data.js。

- workdir 缺 工作台.html 时自动从 assets/工作台模板.html 复制
- 收藏赞比、逐字稿高亮在此自动计算
- --self-test: 用内置夹具在临时目录跑通全流程，不碰真实数据
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
from config_lib import load_config
CFG = load_config()
TZ = timezone(timedelta(hours=8))


def build_transcript(nid: str, workdir: Path, hl: dict) -> list:
    tj = workdir / "transcripts" / f"{nid}.json"
    if not tj.exists():
        return []
    segs = json.loads(tj.read_text(encoding="utf-8"))["segments"]
    zh_path = workdir / "transcripts" / f"{nid}.zh.json"
    zh_list = []
    if zh_path.exists():
        zh_list = json.loads(zh_path.read_text(encoding="utf-8")).get("segments", [])
    hook_kw, gold_kw, cta_kw = hl.get("hook_kw", []), hl.get("gold_kw", []), hl.get("cta_kw", [])
    out = []
    for i, s in enumerate(segs):
        tag = None
        if any(k in s["text"] for k in hook_kw):
            tag = "hook"
        elif any(k in s["text"] for k in gold_kw):
            tag = "gold"
        elif any(k in s["text"] for k in cta_kw):
            tag = "cta"
        item = {"s": s["start"], "e": s["end"], "t": s["text"]}
        if i < len(zh_list):
            zh = zh_list[i].get("zh") if isinstance(zh_list[i], dict) else zh_list[i]
            if zh:
                item["zh"] = zh
        if tag:
            item["hl"] = tag
        out.append(item)
    return out


def build_data(workdir: Path) -> dict:
    analyses = [json.loads(f.read_text(encoding="utf-8"))
                for f in sorted((workdir / "analyses").glob("*.json"))]
    if not analyses:
        raise SystemExit(f"{workdir}/analyses 里没有分析文件")

    cases = []
    for a in analyses:
        c = {k: v for k, v in a.items() if k != "highlights"}
        c["ratio"] = round(a["collects"] / a["likes"], 2) if a["likes"] else 0
        c["transcript"] = build_transcript(a["id"], workdir, a.get("highlights", {}))
        cases.append(c)
    cases.sort(key=lambda c: c["collectedAt"] + c["id"])

    insights_path = workdir / "insights.json"
    insights = []
    if insights_path.exists():
        insights = json.loads(insights_path.read_text(encoding="utf-8")).get("insights", [])

    return {"generatedAt": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
            "insights": insights, "cases": cases}


def write_workbench(workdir: Path):
    workdir.mkdir(parents=True, exist_ok=True)
    html = workdir / "工作台.html"
    if not html.exists():
        shutil.copy(SKILL_DIR / "assets" / "工作台模板.html", html)
        print(f"[模板] 已复制工作台页面到 {html}")

    data = build_data(workdir)
    out = workdir / "data.js"
    out.write_text("window.CASE_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n",
                   encoding="utf-8")

    n_seg = sum(len(c["transcript"]) for c in data["cases"])
    print(f"[完成] {len(data['cases'])} 条案例 / {n_seg} 段逐字稿 → {out}")
    print(f"[使用] 双击打开 {html} 即可")

    cited = {sid for i in data["insights"] for sid in i.get("sources", [])}
    uncited = [c["title"][:20] for c in data["cases"] if c["id"] not in cited]
    if uncited:
        print(f"[提醒] {len(uncited)} 条案例未被运营经验引用（攒到 ≥3 条时应重写 insights.json）：")
        for t in uncited:
            print(f"       - {t}")


def self_test():
    """夹具跑通：临时目录 → 造 transcript/analysis/insights → 构建 → 断言。"""
    fx = SKILL_DIR / "assets" / "self-test"
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "transcripts").mkdir()
        (wd / "analyses").mkdir()
        shutil.copy(fx / "transcript.json", wd / "transcripts" / "selftestcase0000000001.json")
        shutil.copy(fx / "zh.json", wd / "transcripts" / "selftestcase0000000001.zh.json")
        shutil.copy(fx / "analysis.json", wd / "analyses" / "selftestcase0000000001.json")
        shutil.copy(fx / "insights.json", wd / "insights.json")

        data = build_data(wd)
        c = data["cases"][0]
        assert c["id"] == "selftestcase0000000001", "案例 id 不符"
        assert c["ratio"] == 1.5, f"收藏赞比计算错误: {c['ratio']}"
        assert len(c["transcript"]) == 3, "逐字稿段数不符"
        assert c["transcript"][0].get("hl") == "hook", "钩子高亮失败"
        assert c["transcript"][2].get("hl") == "cta", "CTA 高亮失败"
        assert c["transcript"][0].get("zh") == "第一句中文翻译", "中英对照合并失败"
        out = "window.CASE_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
        assert "selftestcase0000000001" in out and "测试钩子" in out
        print("[自测通过] 数据合并/收藏赞比/高亮/insights 全链路 OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        write_workbench(Path(sys.argv[sys.argv.index("--workdir") + 1]) if "--workdir" in sys.argv
                        else Path(CFG["workdir"]))
