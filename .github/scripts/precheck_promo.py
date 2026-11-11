#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Epic 周免变更检测：只有免费阵容真的变了才启动浏览器任务。

设计要点：
- 只读公开促销接口，不携带任何凭据，几秒完成。
- 用「当前免费 offer 的 id + 截止时间」做签名，与上次记录比对。
- 换新空窗期（列表为空）不写签名、不触发，避免抖动。
- 接口异常时不触发，避免把一次网络故障变成一次无意义的浏览器启动。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request

API = ("https://store-site-backend-static-ipv4.ak.epicgames.com/"
       "freeGamesPromotions?locale=zh-CN&country=UM&allowCountries=UM")
STATE = os.path.join(".promo", "signature.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def emit(should_run: bool, reason: str, extra: dict | None = None) -> None:
    print("should_run=%s reason=%s" % (should_run, reason))
    if extra:
        for k, v in extra.items():
            print("  %s: %s" % (k, v))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("should_run=%s\n" % ("true" if should_run else "false"))
            f.write("reason=%s\n" % reason)


def free_offers(payload: dict) -> list[dict]:
    """按促销折扣判定当前免费条目（与领取脚本的判定口径一致）。"""
    elements = (((payload.get("data") or {}).get("Catalog") or {})
                .get("searchStore") or {}).get("elements") or []
    out: list[dict] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        offers: list[dict] = []
        for group in (el.get("promotions") or {}).get("promotionalOffers") or []:
            offers.extend(group.get("promotionalOffers") or [])
        free = [o for o in offers
                if (o.get("discountSetting") or {}).get("discountPercentage") == 0]
        if not free:
            continue
        item = free[0]
        out.append({
            "id": el.get("id") or "",
            "title": el.get("title") or "",
            "start": (item.get("startDate") or "")[:19],
            "end": (item.get("endDate") or "")[:19],
        })
    return out


def signature(offers: list[dict]) -> str:
    raw = json.dumps(
        sorted((o["id"], o["end"]) for o in offers),
        ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_previous() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_current(sig: str, offers: list[dict]) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({
            "signature": sig,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "offers": offers,
        }, f, ensure_ascii=False, indent=1)


def main() -> int:
    req = urllib.request.Request(API, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("::warning::促销接口请求失败：%s" % exc)
        emit(False, "api-error")
        return 0

    offers = free_offers(payload)
    if not offers:
        print("当前没有处于免费期的条目（可能是换新空窗），本次不触发且不覆盖签名")
        emit(False, "empty")
        return 0

    sig = signature(offers)
    prev = load_previous()
    prev_sig = prev.get("signature") or ""

    print("当前免费 %d 项：" % len(offers))
    for o in offers:
        print("  - %s  截止 %s  id=%s" % (o["title"], o["end"], o["id"]))
    print("签名 %s…（上次 %s…）" % (sig[:12], (prev_sig[:12] or "无记录")))

    if sig == prev_sig:
        emit(False, "unchanged")
        return 0

    save_current(sig, offers)
    emit(True, "changed", {"changed_at": time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                                       time.gmtime())})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
