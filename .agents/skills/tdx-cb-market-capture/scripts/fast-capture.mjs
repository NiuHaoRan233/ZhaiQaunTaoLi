import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import sharp from "sharp";

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
  return sky.get_window_state({
    window: state.window,
    include_screenshot: true,
    include_text: false,
  });
}

export async function moveToBoundary({ sky, state, key, maxSteps = 100, region }) {
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
}) {
  await mkdir(outputDir, { recursive: true });
  const pages = [];
  let current = state;
  let currentHash = await stableDataHash(current, region);

  for (let sequence = 1; sequence <= maxPages; sequence += 1) {
    const filename = `${stem}_${String(sequence).padStart(2, "0")}.png`;
    const path = join(outputDir, filename);
    await writeFile(path, screenshotBytes(screenshotFrom(current)), { flag: "wx" });
    pages.push({ sequence, path, hash: currentHash });

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
