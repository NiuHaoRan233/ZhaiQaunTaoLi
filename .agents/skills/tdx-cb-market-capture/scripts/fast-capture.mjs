import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
// Inject the runtime's already loaded CommonJS sharp. A static ESM import
// can fail in node_repl on sharp's package.json import and waste the capture.
let sharp;

export function configureCapture({ imageProcessor }) {
  if (typeof imageProcessor !== "function") throw new Error("A loaded sharp function is required");
  sharp = imageProcessor;
}

function screenshotFrom(state) {
  const screenshot = state?.screenshots?.[0];
  if (!screenshot?.url) throw new Error("Fresh screenshot is required");
  return screenshot;
}

function screenshotBytes(screenshot) {
  const comma = screenshot.url.indexOf(",");
  if (comma < 0) throw new Error("Invalid screenshot data URL");
  return Buffer.from(screenshot.url.slice(comma + 1), "base64");
}

export async function stableDataHash(state, region = {}) {
  if (!sharp) throw new Error("Call configureCapture({ imageProcessor: sharp }) first");
  const input = screenshotBytes(screenshotFrom(state));
  const metadata = await sharp(input).metadata();
  const width = metadata.width ?? 0;
  const height = metadata.height ?? 0;
  const left = region.left ?? 0;
  const top = region.top ?? 52;
  const right = region.right ?? 0;
  const bottom = region.bottom ?? 34;
  const cropWidth = width - left - right;
  const cropHeight = height - top - bottom;
  if (cropWidth <= 0 || cropHeight <= 0) throw new Error("Invalid stable hash crop");

  const pixels = await sharp(input)
    .extract({ left, top, width: cropWidth, height: cropHeight })
    .removeAlpha()
    .raw()
    .toBuffer();
  return createHash("sha256").update(pixels).digest("hex");
}

async function pressAndRefresh(sky, state, key) {
  if (!state?.window) throw new Error("Fresh window state is required");
  await sky.press_key({ window: state.window, key });
  const next = await sky.get_window_state({
    window: state.window,
    include_screenshot: true,
    include_text: false,
  });
  if (next.window?.id !== state.window.id || next.window?.app !== state.window.app) {
    throw new Error("Capture window changed; reselect and observe before continuing");
  }
  const before = await sharp(screenshotBytes(screenshotFrom(state))).metadata();
  const after = await sharp(screenshotBytes(screenshotFrom(next))).metadata();
  if (before.width !== after.width || before.height !== after.height) {
    throw new Error("Screenshot dimensions changed; recalibrate the view");
  }
  return next;
}

export async function moveToBoundary({ sky, state, key, maxSteps = 100, region }) {
  if (!["PageUp", "PageDown"].includes(key)) throw new Error("Only paging keys are supported");
  let current = state;
  let currentHash = await stableDataHash(current, region);

  for (let steps = 1; steps <= maxSteps; steps += 1) {
    const next = await pressAndRefresh(sky, current, key);
    const nextHash = await stableDataHash(next, region);
    if (nextHash === currentHash) {
      return { state: next, steps, boundaryConfirmed: true };
    }
    current = next;
    currentHash = nextHash;
  }
  throw new Error(`Boundary not found within ${maxSteps} ${key} actions`);
}

export async function captureForward({
  sky,
  state,
  outputDir,
  stem,
  maxPages = 100,
  region,
  normalizedDir,
  normalizedStem,
}) {
  await mkdir(outputDir, { recursive: true });
  if (normalizedDir) {
    if (!normalizedStem) throw new Error("normalizedStem is required with normalizedDir");
    await mkdir(normalizedDir, { recursive: true });
  }
  const pages = [];
  let current = state;
  let currentHash = await stableDataHash(current, region);

  for (let sequence = 1; sequence <= maxPages; sequence += 1) {
    const bytes = screenshotBytes(screenshotFrom(current));
    const format = (await sharp(bytes).metadata()).format;
    const extension = format === "jpeg" ? "jpg" : format === "png" ? "png" : null;
    if (!extension) throw new Error(`Unsupported screenshot encoding: ${format}`);
    const suffix = String(sequence).padStart(2, "0");
    const filename = `${stem}_${suffix}.${extension}`;
    const path = join(outputDir, filename);
    await writeFile(path, bytes, { flag: "wx" });
    let normalized;
    if (normalizedDir) {
      normalized = join(normalizedDir, `${normalizedStem}_${suffix}.png`);
      await writeFile(normalized, await sharp(bytes).png().toBuffer(), { flag: "wx" });
    }
    pages.push({ sequence, path, normalized, encoding: format, hash: currentHash,
      capturedAt: new Date().toISOString() });

    const next = await pressAndRefresh(sky, current, "PageDown");
    const nextHash = await stableDataHash(next, region);
    if (nextHash === currentHash) {
      return { state: next, pages, boundaryConfirmed: true };
    }
    current = next;
    currentHash = nextHash;
  }
  throw new Error(`Final page not found within ${maxPages} pages`);
}
