# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import suppress

import pyotp
from loguru import logger
from playwright.async_api import Locator, Page, expect

TOTP_INPUT_SELECTORS = (
    "input[autocomplete='one-time-code']",
    "input[name='code']",
    "input[id*='code']",
    "input[inputmode='numeric']",
    "input[type='tel']",
)
GENERIC_TEXT_INPUT_SELECTORS = ("input[type='text']",)
TOTP_MIN_SECONDS_REMAINING = 12


def _totp_secret_value() -> str | None:
    secret = os.getenv("EPIC_TOTP_SECRET", "").replace(" ", "").strip()
    return secret or None


def totp_login_enabled() -> bool:
    return _totp_secret_value() is not None


async def _current_totp_code(page: Page, *, force_next_window: bool = False) -> str | None:
    secret = _totp_secret_value()
    if not secret:
        return None

    try:
        totp = pyotp.TOTP(secret)
        remaining = totp.interval - (time.time() % totp.interval)
        if force_next_window or remaining < TOTP_MIN_SECONDS_REMAINING:
            logger.warning(
                "Waiting for fresh Epic authenticator TOTP window | reason={} "
                "seconds_until_next={:.1f}",
                "invalid_retry" if force_next_window else "near_expiry",
                remaining,
            )
            await page.wait_for_timeout(int((remaining + 1) * 1000))
            remaining = totp.interval - (time.time() % totp.interval)
        logger.debug("Generated Epic authenticator TOTP code | seconds_remaining={:.1f}", remaining)
        return totp.now()
    except Exception as err:
        logger.error(
            "Failed to generate Epic authenticator TOTP code; EPIC_TOTP_SECRET is likely "
            "not a valid base32 secret | error_type={}",
            type(err).__name__,
        )
        return None


async def _select_authenticator_mfa_method(page: Page) -> bool:
    with suppress(Exception):
        clicked = await page.evaluate(
            """
            () => {
              const normalize = (value) =>
                (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
              const isVisible = (element) => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' &&
                  style.display !== 'none' &&
                  style.opacity !== '0';
              };
              const preferred = ['authenticator', 'authentication app', 'verification app'];
              const candidates = Array.from(document.querySelectorAll('button,a,label'))
                .filter(isVisible)
                .filter((element) => {
                  const text = normalize(element.innerText || element.textContent);
                  return preferred.some((marker) => text.includes(marker));
                });
              const target = candidates[0];
              if (!target) {
                return false;
              }
              target.click();
              return true;
            }
            """
        )
        if clicked:
            await page.wait_for_timeout(1000)
            return True
    return False


async def _page_has_mfa_signal(page: Page) -> bool:
    with suppress(Exception):
        return await page.evaluate(
            """
            () => {
              const text = (document.body?.innerText || '').toLowerCase();
              const href = window.location.href.toLowerCase();
              const markers = [
                'two-factor',
                'two factor',
                'authentication code',
                'verification code',
                'security code',
                'authenticator',
                'enter code',
              ];
              return href.includes('/id/login/mfa') ||
                markers.some((marker) => text.includes(marker));
            }
            """
        )
    return False


async def _visible_indexes(page: Page, selector: str, limit: int) -> list[int]:
    locator = page.locator(selector)
    indexes: list[int] = []
    count = await locator.count()
    for index in range(count):
        with suppress(Exception):
            if await locator.nth(index).is_visible(timeout=250):
                indexes.append(index)
                if len(indexes) >= limit:
                    break
    return indexes


async def _has_visible_totp_input(page: Page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        if await _visible_indexes(page, selector, limit=1):
            return True
    return False


async def _focus_totp_entry(page: Page) -> bool:
    with suppress(Exception):
        return await page.evaluate(
            """
            () => {
              const isVisible = (element) => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' &&
                  style.display !== 'none' &&
                  style.opacity !== '0';
              };
              const editables = Array.from(document.querySelectorAll(
                "input, textarea, [contenteditable='true'], [role='textbox']"
              )).filter(isVisible);
              const narrowEditables = editables.filter((element) => {
                const rect = element.getBoundingClientRect();
                return rect.width >= 24 && rect.width <= 90 &&
                  rect.height >= 24 && rect.height <= 90;
              });
              const target = editables.find((element) => {
                const marker = [
                  element.getAttribute('autocomplete'),
                  element.getAttribute('name'),
                  element.getAttribute('id'),
                  element.getAttribute('inputmode'),
                  element.getAttribute('aria-label'),
                ].join(' ').toLowerCase();
                const isCodeField = marker.includes('code') ||
                  marker.includes('numeric') ||
                  marker.includes('one-time');
                return isCodeField ||
                  (narrowEditables.length >= 6 && narrowEditables.includes(element));
              });
              if (!target) {
                return false;
              }
              target.style.setProperty('-webkit-text-security', 'disc', 'important');
              target.style.setProperty('color', 'transparent', 'important');
              target.style.setProperty('caret-color', 'transparent', 'important');
              target.click();
              return true;
            }
            """
        )
    return False


async def _mask_totp_input(field: Locator) -> bool:
    try:
        await field.evaluate(
            """
            (element) => {
              element.style.setProperty('-webkit-text-security', 'disc', 'important');
              element.style.setProperty('color', 'transparent', 'important');
              element.style.setProperty('caret-color', 'transparent', 'important');
            }
            """
        )
        return True
    except Exception as err:
        logger.error(
            "Could not mask Epic authenticator TOTP input before filling | error_type={}",
            type(err).__name__,
        )
        return False


async def _clear_totp_entry(page: Page) -> None:
    focused = False
    with suppress(Exception):
        focused = await page.evaluate(
            """
            () => {
              const isVisible = (element) => {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                return rect.width > 0 && rect.height > 0 &&
                  style.visibility !== 'hidden' &&
                  style.display !== 'none' &&
                  style.opacity !== '0';
              };
              const candidates = Array.from(document.querySelectorAll(
                "input, [contenteditable='true'], [role='textbox']"
              ))
                .filter(isVisible)
                .filter((element) => {
                  const rect = element.getBoundingClientRect();
                  const marker = [
                    element.getAttribute('autocomplete'),
                    element.getAttribute('name'),
                    element.getAttribute('id'),
                    element.getAttribute('inputmode'),
                    element.getAttribute('type'),
                    element.getAttribute('aria-label'),
                  ].join(' ').toLowerCase();
                  const looksLikeCodeBox = rect.width >= 20 && rect.width <= 120 &&
                    rect.height >= 20 && rect.height <= 120;
                  return looksLikeCodeBox ||
                    marker.includes('code') ||
                    marker.includes('numeric') ||
                    marker.includes('one-time');
                });

              for (const element of candidates) {
                if ('value' in element) {
                  element.value = '';
                  element.dispatchEvent(new Event('input', { bubbles: true }));
                  element.dispatchEvent(new Event('change', { bubbles: true }));
                } else if (element.isContentEditable) {
                  element.textContent = '';
                  element.dispatchEvent(new Event('input', { bubbles: true }));
                }
              }

              const target = candidates[0];
              if (target) {
                target.click();
                return true;
              }
              return false;
            }
            """
        )

    if focused:
        with suppress(Exception):
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")


async def redact_totp_inputs(page: Page) -> None:
    if not totp_login_enabled():
        return
    if await _page_has_mfa_signal(page):
        await _clear_totp_entry(page)


async def _wait_for_totp_input(page: Page, timeout_ms: int = 20000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        await _select_authenticator_mfa_method(page)

        if await _has_visible_totp_input(page, TOTP_INPUT_SELECTORS):
            return True

        if await _page_has_mfa_signal(page) and await _has_visible_totp_input(
            page, GENERIC_TEXT_INPUT_SELECTORS
        ):
            return True

        if await _page_has_mfa_signal(page):
            return True

        await page.wait_for_timeout(500)

    return False



async def _dump_totp_debug(page: Page, stage: str) -> None:
    """[diag] 转储 2FA 页面现场：DOM 快照 + 选择器命中清单。"""
    try:
        import json as _json

        base = os.path.join("app", "volumes", "runtime", "totp_debug")
        os.makedirs(base, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        data = {"stage": stage, "url": page.url, "title": await page.title()}
        census = []
        for sel in TOTP_INPUT_SELECTORS + ("input[type='text']", "input[type='password']"):
            loc = page.locator(sel)
            try:
                total = await loc.count()
            except Exception:
                total = -1
            vis = []
            for idx in range(min(max(total, 0), 24)):
                el = loc.nth(idx)
                try:
                    if not await el.is_visible(timeout=200):
                        continue
                    vis.append(await el.evaluate(
                        "e => ({name: e.name, id: e.id, type: e.type,"
                        " inputmode: e.getAttribute('inputmode'),"
                        " autocomplete: e.getAttribute('autocomplete'),"
                        " value: e.value, readonly: e.readOnly, disabled: e.disabled,"
                        " rect: (r => ({x: Math.round(r.x), y: Math.round(r.y),"
                        " w: Math.round(r.width), h: Math.round(r.height)}))"
                        "(e.getBoundingClientRect())})"))
                except Exception:
                    continue
            census.append({"selector": sel, "count": total, "visible": vis})
        data["census"] = census
        with open(os.path.join(base, stamp + "_" + stage + ".json"), "w",
                  encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        with open(os.path.join(base, stamp + "_" + stage + ".html"), "w",
                  encoding="utf-8") as f:
            f.write(await page.content())
        logger.info("[diag] TOTP debug dump OK | stage={} | url={}", stage, page.url[:120])
    except Exception as err:
        logger.warning("[diag] TOTP debug dump failed | stage={} | error_type={}",
                       stage, type(err).__name__)



async def _refill_totp_code(page: Page, code: str) -> bool:
    """[fix2] hCaptcha 拖延 MFA 提交期间，用新窗口的码重填验证码框。"""
    selector = "input[autocomplete='one-time-code']"
    try:
        locator = page.locator(selector)
        count = await locator.count()
        fields = []
        for index in range(count):
            field = locator.nth(index)
            if await field.is_visible(timeout=250):
                fields.append(field)
            if len(fields) >= len(code):
                break
        if not fields:
            return False
        await _clear_totp_entry(page)
        if len(fields) >= len(code):
            fields = fields[: len(code)]
            for field, digit in zip(fields, code):
                with suppress(Exception):
                    await field.click(timeout=800)
                await field.press_sequentially(digit, delay=40, timeout=1500)
        else:
            field = fields[0]
            with suppress(Exception):
                await field.click(timeout=800)
            await field.press_sequentially(code, delay=60, timeout=8000)
        return True
    except Exception as err:
        logger.warning("[fix2] refill failed | error_type={}", type(err).__name__)
        return False


def _watch_mfa_responses(page: Page) -> None:
    """[diag] 记录 MFA 提交相关的全部网络响应（URL + 状态码）。"""
    def _on_response(r):
        try:
            url = r.url
            if "/id/api/" in url or "/id/login" in url or "accounts.epicgames.com" in url:
                logger.info(
                    "[diag] net | {} {} | status={}",
                    r.request.method, url[:140], r.status,
                )
        except Exception:
            pass
    try:
        page.on("response", _on_response)
        logger.info("[diag] MFA response watcher attached")
    except Exception as err:
        logger.warning("[diag] watcher attach failed | error_type={}", type(err).__name__)


async def submit_totp_challenge(
    page: Page, *, force_next_code: bool = False, before_submit: Callable[[], None] | None = None
) -> bool:
    if not totp_login_enabled():
        return False

    if not await _wait_for_totp_input(page):
        logger.error("Could not find Epic authenticator 2FA code input after waiting")
        return False

    _watch_mfa_responses(page)
    await _dump_totp_debug(page, "1_before_fill")
    await _clear_totp_entry(page)

    code = await _current_totp_code(page, force_next_window=force_next_code)
    if not code:
        logger.error(
            "Epic account requires authenticator 2FA, but no valid TOTP code could be "
            "generated. Set EPIC_TOTP_SECRET to a valid base32 authenticator secret."
        )
        return False

    selectors = TOTP_INPUT_SELECTORS
    if await _page_has_mfa_signal(page):
        selectors += GENERIC_TEXT_INPUT_SELECTORS

    filled = False
    for selector in selectors:
        locator = page.locator(selector)
        try:
            indexes = await _visible_indexes(page, selector, limit=max(len(code), 1))
            if not indexes:
                continue

            if len(indexes) >= len(code):
                fields = [locator.nth(index) for index in indexes[: len(code)]]
                if not all([await _mask_totp_input(field) for field in fields]):
                    continue
                # [fix] Epic 的 MUI OTP 框是 React 受控组件：fill() 设置的 DOM
                # 值会被重渲染丢弃（run 42460864345 快照证实），必须用真实键盘
                # 事件驱动 onChange 更新 React state，Continue 才会解禁。
                for field, digit in zip(fields, code):
                    with suppress(Exception):
                        await field.click(timeout=1000)
                    await field.press_sequentially(digit, delay=60, timeout=2000)
            else:
                field = locator.nth(indexes[0])
                await expect(field).to_be_visible(timeout=2000)
                if not await _mask_totp_input(field):
                    continue
                await field.press_sequentially(code, delay=80, timeout=10000)
            filled = True
            break
        except Exception:
            continue

    if not filled and await _page_has_mfa_signal(page):
        if await _focus_totp_entry(page):
            await page.keyboard.type(code, delay=80)
            filled = True
        else:
            logger.error("Could not focus a masked Epic authenticator 2FA code input")

    if not filled:
        logger.error("Could not find Epic authenticator 2FA code input")
        return False

    await _dump_totp_debug(page, "2_after_fill")

    if before_submit:
        before_submit()

    async def _click_continue() -> bool:
        for selector in (
            "#continue",
            "#sign-in",
            "button[type='submit']",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify')]",
        ):
            with suppress(Exception):
                button = page.locator(selector).first
                if await button.is_visible(timeout=1000):
                    await button.click(timeout=2000)
                    return True
        return False

    clicked = await _click_continue()
    if not clicked:
        with suppress(Exception):
            await page.keyboard.press("Enter")
            clicked = True

    if clicked:
        # [fix] 禁用的 Continue 会吞掉点击：提交后若验证码框仍可见则重试点击，
        # 防止 React 状态未更新时静默失败。
        for retry in range(2):
            await page.wait_for_timeout(2500)
            if not await _has_visible_totp_input(page, TOTP_INPUT_SELECTORS):
                break
            logger.warning(
                "2FA form still visible after submit; retrying Continue | retry={}",
                retry + 1,
            )
            if not await _click_continue():
                with suppress(Exception):
                    await page.keyboard.press("Enter")
        logger.success("Submitted Epic authenticator 2FA code")
        # [fix3] 重试引擎已上收至 outcome 循环（码框可见即填新码+点击）；
        # 此处点击后立即返回，避免阻塞 outcome 的状态机轮转。
        await page.wait_for_timeout(2000)
        await _dump_totp_debug(page, "4_after_settle")
    return clicked
