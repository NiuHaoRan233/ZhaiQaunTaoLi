---
name: tdx-cb-market-capture
description: Operate the Windows 通达信金融终端 in read-only mode to capture full-day 逐笔委托 and 逐笔成交 data for 132026.SH G三峡EB2 and 132024.SH 26江铜EB. Use when the user asks to view, inspect, screenshot, collect, or automate either or both verified exchangeable-bond workflows on this or another Windows computer.
---

# 通达信交换债数据采集

Use the `computer-use:computer-use` skill for every Windows UI action. Read its current instructions before controlling 通达信. Re-observe after every state change; never reuse stale screenshot IDs, coordinates, or accessibility indexes.

Treat this repository copy as the portable source of truth. Do not assume a Windows username, drive letter, 通达信 installation path, display resolution, or window size from a previous computer.

## Daily targets

- `132026.SH` — `G三峡EB2`
- `132024.SH` — `26江铜EB`
- Application identity: `通达信金融终端` / `tdxw.exe`
- Required views: bottom `逐笔委托`; lower-right `细`, meaning `成交明细`

When the user asks for the daily collection without limiting the security, capture both targets. Treat the visible 通达信 display name as authoritative and include it in the security-level output folder.

Never save passwords, account credentials, or authentication screenshots. Do not inspect masked credentials.

## Safety boundary

Keep this workflow strictly read-only. Never open or operate trading, order-entry, broker-order, or entrustment functions. Do not import or call `xttrader`. Never send broker orders. A UI label containing `委托` in `逐笔委托` is market-data analysis, not authorization to enter an order screen.

Stop if the desktop is locked, a credential prompt requires user input, the requested code/display-name pair cannot be verified, or the layout is materially different. Do not guess coordinates across layouts.

## Discover and calibrate on each computer

1. List installed/running apps and select 通达信 by its returned display name, executable name, and window title. Do not construct a path from memory.
2. If 通达信 is installed but absent from app discovery, ask for its executable location once. Use that returned/user-provided path only on that computer; do not write it into the repository skill.
3. Maximize or consistently size the window when practical, then obtain a fresh screenshot. Locate controls by visible labels and surrounding regions. Use recorded coordinates only as rough hints for the previously verified layout.
4. Verify the target by visible code and name after navigation. A successful calibration must match one of the code/name pairs above.

## Verified navigation workflow

1. Select exactly one returned 通达信 window. If absent, launch the discovered executable, wait briefly, list apps/windows again, and select exactly one returned window.
2. If the login dialog is visible and the account and password are already filled, click `登录` without reading or changing either field. Wait for the main行情 window. If a new authentication, permission, or credential action is needed, follow the Computer Use confirmation policy.
3. Activate the main行情 window and observe it.
4. Focus the行情 document area. Start keyboard search with one numeric key, observe the `通达信键盘精灵`, then enter the remaining digits using individual key presses. Direct bulk text entry did not reliably open keyboard search in the verified installation.
5. Enter the six-digit target code and verify the result row matches the expected display name and `上海债券`; press `Return` once.
6. Verify the title/security area shows the requested code and matching display name before continuing.
7. Ensure the page is `分时`. 通达信 may restore directly to this view. If it opens a 日线/analysis view, press `Return` once and re-observe; continue only after the 分时 chart is visible.
8. At the bottom of the chart, click `逐笔委托`. Re-observe and verify dense time/price/size rows appear under the chart.
9. At the lower-right view selector, click the single character `细`. Re-observe and verify `细` is highlighted blue and the right pane shows成交明细.

On the originally verified 1688×1015 window, `逐笔委托` was near `(228, 984)` and `细` near `(1534, 983)`. Never use these coordinates without a fresh screenshot and visual confirmation on the current computer.

## Capture views

For a complete post-close daily capture, read and follow [references/full-day-capture.md](references/full-day-capture.md). Do not replace that workflow with a few representative screenshots.

Preserve a full-window screenshot showing the security identity and combined 分时 view when audit context matters.

For a clearer `逐笔委托` capture, double-click inside its data rows. Verify the full-screen title contains the target code, display name, and `逐笔委托明细`, capture it, then double-click inside that view to restore the combined page.

For a clearer `成交明细` capture, double-click inside the right-side成交明细 data pane. Verify the full-screen title contains the target code, display name, and `逐笔成交明细`, then capture it. Do not double-click a record to exit: doing so opens a read-only `买入成交追踪` popup. Exit the full-screen成交明细 view using the small close mark at the far left of its internal title bar. If the成交追踪 popup appears, close its own top-right `×`, re-observe, then use the internal title-bar close mark.

After capture, verify the application still shows the target code/name pair and no trading window is open. Leave the combined 分时 page with `逐笔委托` and `细` selected unless the user asks otherwise.

## Portable output rules

Resolve all output paths relative to the repository root or from local `config.toml`; never embed a drive letter or username. Keep captured images and generated OCR/structured data in ignored local runtime directories. Use `成交委托数据截图保存/<代码.SH_显示名>/<YYYY-MM-DD>/` so each security contains its own dated captures.

For recurring collection, require the Windows session to remain logged in and unlocked and 通达信 to be able to connect. Save original images with timestamps and retain them for audit. If OCR or structured extraction is requested, retain the source screenshot, include capture time and security code, and never silently substitute low-confidence values.

Do not commit `config.toml`, captured images, account data, logs, SQLite runtime databases, or backups. Migrate live SQLite state only through `zhaiquant backup`; never copy a live WAL database directly.
