# 收盘后完整逐页截图

Use this workflow when the user asks for the complete trading day, daily screenshots, post-close collection, or screenshots for later model research.

## Performance target and fast path

Treat speed as part of correctness. After 通达信 is open, connected, and showing the verified security, aim to finish both datasets in about 60 seconds on the known layout. Do not narrate or emit every page while collecting.

Prefer the bundled `scripts/fast-capture.mjs` after visually verifying the full-screen title and data pane. Import it in the persistent `node_repl` session and use its boundary and forward-capture helpers. The helper presses only one paging key at a time, obtains a fresh screenshot after every key press, hashes the stable data rectangle, saves every changed page immediately, and stops only when the cropped data is unchanged. This preserves the Computer Use fresh-observation requirement without a model/tool round trip per page.

Use the fast path only post-close, when the data is no longer changing. Crop out the top title/clock area and bottom status bar for boundary hashing. Keep the mouse stationary and use only `PageUp` / `PageDown` while the batch is running. If the full-screen view, security identity, window, or screenshot dimensions change, abandon the batch and return to the manual verified flow below.

Save lossless sequence-first files during the batch, such as `2026-08-14_132026.SH_逐笔委托_01.png`. After both batches finish, derive visible boundary times and rename/write the index in one pass. If reliable automatic time extraction is unavailable, keep the sequence-first filenames and record the times in `截图索引.md`; never slow or risk the actual page capture merely to construct filenames.

## Output layout

Use the repository-local root `成交委托数据截图保存/`. Separate securities first, then create folders by the actual market date shown in the data, not by the after-midnight capture time:

```text
成交委托数据截图保存/
├── 132026.SH_G三峡EB2/<YYYY-MM-DD>/
│   ├── 逐笔委托/
│   ├── 逐笔成交/
│   └── 截图索引.md
└── 132024.SH_26江铜EB/<YYYY-MM-DD>/
    ├── 逐笔委托/
    ├── 逐笔成交/
    └── 截图索引.md
```

Name each image as:

`交易日_证券代码.SH_数据类型_画面最早时间-画面最晚时间_两位序号.png`

Keep this output ignored by Git. Never overwrite an existing official page silently; compare it first or use a clearly marked retry filename.

## Capture one dataset

Apply the following procedure separately to `逐笔委托` and `逐笔成交`:

The steps below are the manual fallback and audit definition. The fast helper must produce the same page sequence and boundary result.

1. Verify the screen identifies the requested code and its matching display name.
2. Select the required combined-page view (`逐笔委托` at bottom, or `细` for成交明细), then double-click its data area to enter the full-screen detail view.
3. Verify the internal title says `逐笔委托明细` or `逐笔成交明细`. The verified title also shows `Up/PageUp/滚轮 前翻` and `Down/PageDown/滚轮 后翻`.
4. Press `PageUp` once per observation until the visible data boundaries no longer move. Compare the first and last visible record times/rows, not the full screenshot bytes, because the window clock and cursor can change. Confirm this is the actual earliest page.
5. Accept the earliest record actually present. A quiet day may start after 09:30; 委托 may include 09:15 auction records. Never discard earlier same-day records merely to force a 09:30 label.
6. Capture and save the earliest page as sequence `01` before moving it.
7. Press `PageDown` exactly once, re-observe, and save the next page. Repeat one page at a time. Do not issue multiple PageDown actions without capturing every intermediate state.
8. For each transition, record the previous page's last visible timestamp/row and the next page's first visible timestamp/row. Small overlap is preferred and must be retained. A timestamp jump can be a period with no events, so use the actual boundary rows and the deterministic one-page action; never infer a missing page from time alone.
9. If a page appears unchanged after `PageDown`, compare its visible boundary rows with the prior page. When unchanged, the prior saved page is the final page; do not save endless duplicates.
10. Confirm the final saved page reaches the day's actual last record, normally at or after 15:00 and sometimes displayed near 15:30. Do not stop merely because the page looks mostly complete.

Never double-click a成交 record to exit full screen; that opens `买入成交追踪`. Use the small close mark at the far left of the internal full-screen title bar.

## Completeness audit

After both datasets are captured:

1. Write `截图索引.md` with one row per image: relative path, first visible record, last visible record, and overlap/boundary note.
2. Confirm sequence numbers are contiguous from `01` with no missing filename.
3. Confirm the first page is the topmost page and the last page is the bottommost page for both datasets.
4. Confirm every page change came from exactly one `PageDown` or one wheel-page action and every intermediate state was saved.
5. List intentional overlaps; tell the downstream model not to double-count them.
6. If any page, boundary, or file write is uncertain, mark the day incomplete and recapture. Never label a partial set complete.

The 2026-08-14 UI trial verified that `PageUp` moves toward earlier records and `PageDown` moves toward later records in both full-screen views. On that day, the earliest成交 page began at 09:31:51; this correctly represented the first visible成交 rather than a missing 09:30 page.
