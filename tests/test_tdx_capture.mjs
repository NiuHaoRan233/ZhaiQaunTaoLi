import assert from "node:assert/strict";
import { test } from "node:test";
import sharp from "sharp";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { configureCapture, captureForward, moveToBoundary } from "../.agents/skills/tdx-cb-market-capture/scripts/fast-capture.mjs";

configureCapture({ imageProcessor: sharp });
const region = { top: 0, bottom: 0 };
const window = { id: 7, app: "test-tdx" };
async function frame(color, width = 100) {
  const bytes = await sharp({ create: { width, height: 100, channels: 3, background: color } }).jpeg().toBuffer();
  return { window, screenshots: [{ url: `data:image/jpeg;base64,${bytes.toString("base64")}` }] };
}
function fakeSky(frames) {
  let index = 0;
  const calls = [];
  return { calls, press_key: async ({ key }) => calls.push(key),
    get_window_state: async () => { calls.push("fresh"); return frames[index++]; } };
}
test("captures each changed page, stops on stable data, preserves JPEG and writes PNG", async () => {
  const a = await frame("red"), b = await frame("blue");
  const sky = fakeSky([b, b]);
  const folder = await mkdtemp(join(tmpdir(), "tdx-capture-test-"));
  try {
    const result = await captureForward({ sky, state: a, region,
      outputDir: folder, stem: "raw", normalizedDir: join(folder, "normalized"), normalizedStem: "trade" });
    assert.equal(result.pages.length, 2);
    assert.equal(result.boundaryConfirmed, true);
    assert.deepEqual(sky.calls, ["PageDown", "fresh", "PageDown", "fresh"]);
    const page = result.pages[0];
    assert.equal((await sharp(await readFile(page.path)).metadata()).format, "jpeg");
    assert.equal((await sharp(await readFile(page.normalized)).metadata()).format, "png");
    assert.deepEqual(await sharp(await readFile(page.path)).raw().toBuffer(),
      await sharp(await readFile(page.normalized)).raw().toBuffer());
    await assert.rejects(captureForward({ sky: fakeSky([]), state: a, region,
      outputDir: folder, stem: "raw" }), /EEXIST/);
  } finally {
    assert.ok(folder.startsWith(join(tmpdir(), "tdx-capture-test-")));
    await rm(folder, { recursive: true });
  }
});
test("boundary search rejects layout changes and nonpaging keys", async () => {
  const a = await frame("red"), resized = await frame("blue", 110);
  await assert.rejects(moveToBoundary({ sky: fakeSky([resized]), state: a,
    key: "PageUp", region }), /dimensions changed/);
  await assert.rejects(moveToBoundary({ sky: fakeSky([]), state: a,
    key: "Return", region }), /Only paging/);
});
