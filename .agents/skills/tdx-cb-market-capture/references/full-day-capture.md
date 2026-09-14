# 收盘后完整逐页截图

Use this workflow when the user asks for the complete trading day, daily screenshots, post-close collection, or screenshots for later model research.

## Performance target and fast path

Treat speed as part of correctness. After 通达信 is open and connected, aim to finish
both securities' four datasets in about 60 seconds on the known layout. Measure
from the first app discovery/navigation action through the last saved screenshot;
also record OCR/review separately. Do not present paging-helper time as total task
time. Do not narrate or emit every page while collecting.

Complete all four screenshot batches before OCR, directory reports, or parser
debugging. Report the screenshot milestone immediately; then finish the default
OCR/review task unless the user requested screenshots only. If an entrance is not
obvious, inspect the bottom row beside `资金流向` at native resolution once before
trying unrelated menus. Do not repeatedly explore the same failed route.

Prefer the bundled `scripts/fast-capture.mjs` after visually verifying the full-screen title and data pane. Import it in the persistent `node_repl` session and use its boundary and forward-capture helpers. The helper presses only one paging key at a time, obtains a fresh screenshot after every key press, hashes the stable data rectangle, saves every changed page immediately, and stops only when the cropped data is unchanged. This preserves the Computer Use fresh-observation requirement without a model/tool round trip per page.

### Load once in node_repl

Load `sharp` using `createRequire` from the Node runtime directory returned by
`load_workspace_dependencies`, then inject it into the helper. Do not use a static
ESM `import sharp` or rewrite the helper into a temporary module after an import
failure. The Node dependency path is discovered per computer, not stored here.

```js
var { createRequire } = await import('node:module');
var { pathToFileURL } = await import('node:url');
var path = await import('node:path');
// dependencyNodeRoot = parent of the discovered node_modules directory.
var runtimeRequire = createRequire(path.join(dependencyNodeRoot, 'capture.cjs'));
var sharp = runtimeRequire('sharp');
var fast = await import(pathToFileURL(path.join(nodeRepl.cwd,
  '.agents/skills/tdx-cb-market-capture/scripts/fast-capture.mjs')).href);
fast.configureCapture({ imageProcessor: sharp });
```

Use `moveToBoundary({sky, state, key:'PageUp', region})`, retain its returned
`state`, then `captureForward` with that state. On the verified 2560×1392 detail
view, `region:{top:50,bottom:24}` excludes clocks/status; recheck other layouts.
Set `outputDir` to the security root and a timestamped `stem` for original bytes;
set `normalizedDir` to `<security>/<date>/<kind>` and `normalizedStem` to
`<date>_<code>_<kind>` for actual PNG copies. The helper preserves original JPEG
as `.jpg`, writes normalized PNG in the same pass, refuses overwrites, and records
capture timestamps. Save the returned page log once per batch.

Use the fast path only post-close, when the data is no longer changing. Crop out the top title/clock area and bottom status bar for boundary hashing. Keep the mouse stationary and use only `PageUp` / `PageDown` while the batch is running. If the full-screen view, security identity, window, or screenshot dimensions change, abandon the batch and return to the manual verified flow below.

Save sequence-first PNG copies during the batch, such as `2026-08-14_132026.SH_逐笔委托_01.png`.
The PNG retains the decoded pixels; it cannot restore detail lost in a source JPEG.
After all batches finish, derive visible boundary times and write the index in one
pass. Keep times in `截图索引.md`, not in filenames; never slow capture for naming.

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

`交易日_证券代码.SH_数据类型_两位序号.png`

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

## OCR after the capture milestone

- At 2560×1392, the 2026-09-07 order view has **eight** 320-pixel panels; older
  captures at the same resolution have ten. Visually count columns and use
  `tdx-extract-orders --panels 8` only for the eight-panel view. The parser retains
  the historical default; no per-day monkeypatch is needed.
- Pass `--cache-dir <ignored-local-cache>` to both extract commands. Raw OCR tokens
  are cached by exact panel pixels and OCR version before coordinate normalization.
  Fixing parsing or reviewing a row should reuse these tokens, not run all OCR again.
  Use a new output directory for a retry; preserve first-pass CSV and review files.
- Check screenshot counts, parsed rows per column, remaining review flags and
  displayed `总笔` immediately after first extraction. This layout had 77 order
  rows or 75 trade rows per full column; partial/empty last columns are legitimate.
- Trade direction comes from the quantity's B/S suffix, never from price colour.
  Malformed quantity text enters review even when OCR confidence is high.
- Trade overlap is matched as a consecutive time/price/size/direction sequence.
  Same-second events after the last saved row must survive. An OCR mismatch in
  the overlap is retained for review; apply the verified correction, then repeat
  overlap matching from cached tokens rather than deleting rows by time.
- Batch review crops from saved originals, retaining preceding timestamps for
  inherited-time rows. Do not navigate 通达信 again for an OCR-only discrepancy.
