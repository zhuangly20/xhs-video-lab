#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享配置加载：config.json 不存在时自动从 config.example.json 初始化。"""
import json
import shutil
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"


def load_config(quiet=False) -> dict:
    if not CONFIG_PATH.exists():
        example = SKILL_DIR / "config.example.json"
        if not example.exists():
            raise SystemExit(f"缺少配置文件：{CONFIG_PATH}（且无 config.example.json 模板）")
        shutil.copy(example, CONFIG_PATH)
        if not quiet:
            print(f"[配置] 首次运行：已从模板创建 {CONFIG_PATH}", flush=True)
            print("[配置] 还需要填写 workdir（素材库存放路径）。"
                  "请告诉 AI 你想把素材库放在哪里，由它写入配置。", flush=True)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
