// Render an HTML animation to MP4: several browsers capture frames in parallel and
// the frames stream straight into ffmpeg, so no frame files are written.
// The page must define window.render(t) that draws the page at time t (seconds); it may return a promise.
//
//   node html-frames.mjs page.html out.mp4 --dur 60 [--fps 30 --w 1920 --h 1080 --from 0 --audio mix.wav]
//   node html-frames.mjs page.html --stills checks --times 1.5,12,40      (a few JPEG frames to look at)
//
// Other options: --workers N (parallel pages), --browsers N, --quality 92 (JPEG),
// --encoder auto|nvenc|x264, --chrome <path to Chrome>.
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const options = {}, positional = [];
for (let i = 2; i < process.argv.length; i++) {
  const arg = process.argv[i];
  if (arg.startsWith('--')) options[arg.slice(2)] = process.argv[++i];
  else positional.push(arg);
}
const opt = (name, fallback) => options[name] ?? fallback;
const page = positional[0] && path.resolve(positional[0]);
const output = positional[1] && path.resolve(positional[1]);
const stills = options.stills && path.resolve(options.stills);
if (!page || !fs.existsSync(page) || (!output && !stills)) {
  console.error('用法：node html-frames.mjs page.html out.mp4 --dur 秒数 [--fps 30 --w 1920 --h 1080 --audio 音频]\n' +
                '   或：node html-frames.mjs page.html --stills 目录 --times 1.5,12,40');
  process.exit(2);
}
const fps = +opt('fps', 30), W = +opt('w', 1920), H = +opt('h', 1080), from = +opt('from', 0);
const quality = +opt('quality', 92);
const times = stills ? String(opt('times', '0')).split(',').map(Number)
  : Array.from({ length: Math.round(+opt('dur', NaN) * fps) }, (_, i) => from + i / fps);
if (!times.length || times.some(t => !Number.isFinite(t))) {
  console.error('请用 --dur 给出时长（秒），或用 --times 给出要截的时间点');
  process.exit(2);
}
if (options.audio && !fs.existsSync(options.audio)) {
  console.error('找不到音频文件：' + options.audio);
  process.exit(2);
}
// Measured on a 14-core laptop: one browser with many pages tops out near 37 frames/s;
// five browsers with two pages each roughly double that.
const workers = Math.min(times.length, +opt('workers', Math.min(10, Math.max(2, os.cpus().length - 2))));
const browsers = Math.min(workers, +opt('browsers', Math.ceil(workers / 2)));

// playwright-core is shared through the agent toolbox and installed there on first use.
function loadPlaywright() {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const toolbox = process.env.MEDIA_AGENT_TOOLBOX || path.resolve(here, '../../.runtime/agent-toolbox');
  const shared = path.join(toolbox, 'node');
  const bases = [here, process.cwd(), shared, path.join(toolbox, 'templates', 'html-frame-renderer')];
  for (const base of bases) {
    try {
      return createRequire(path.join(base, 'noop.js'))('playwright-core');
    } catch {}
  }
  console.log('首次使用：正在把 playwright-core 安装到 ' + shared);
  fs.mkdirSync(shared, { recursive: true });
  const install = spawnSync(`npm install --no-audit --no-fund --prefix "${shared}" playwright-core`,
    { shell: true, stdio: 'inherit' });
  if (install.status !== 0) throw new Error('playwright-core 安装失败');
  return createRequire(path.join(shared, 'noop.js'))('playwright-core');
}

async function launch(chromium) {
  const flags = ['--allow-file-access-from-files', '--disable-gpu-vsync', '--font-render-hinting=none'];
  const tries = opt('chrome', null) ? [{ executablePath: opt('chrome') }] : [{ channel: 'chrome' }, { channel: 'msedge' }, {}];
  let last;
  for (const choice of tries) {
    try {
      return await chromium.launch({ ...choice, args: flags });
    } catch (error) {
      last = error;
    }
  }
  throw new Error('找不到可用的 Chrome / Edge。请安装 Google Chrome，或用 --chrome 指定浏览器路径。\n' + last.message);
}

function pickEncoder() {
  const choice = opt('encoder', 'auto');
  const nvenc = ['-c:v', 'h264_nvenc', '-preset', 'p5', '-tune', 'hq', '-rc', 'vbr', '-cq', '19', '-b:v', '0'];
  const x264 = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18'];
  if (choice === 'x264') return x264;
  if (choice === 'nvenc') return nvenc;
  // Builds list h264_nvenc even without an NVIDIA GPU, so try one tiny encode.
  const probe = spawnSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=256x256:d=0.1', ...nvenc, '-f', 'null', '-']);
  return probe.status === 0 ? nvenc : x264;
}

const { chromium } = loadPlaywright();
const started = Date.now();
let ffmpeg = null, ffmpegFailed = null, ffmpegClosed = null;
if (!stills) {
  const audio = opt('audio', null);
  const encoder = pickEncoder();
  ffmpeg = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-c:v', 'mjpeg', '-framerate', String(fps), '-i', '-',
    ...(audio ? ['-i', path.resolve(audio), '-map', '0:v', '-map', '1:a', '-c:a', 'aac', '-b:a', '192k', '-shortest'] : []),
    // JPEG frames are full-range; standard-range yuv420p plays everywhere.
    ...encoder, '-vf', 'scale=out_range=tv,format=yuv420p', '-movflags', '+faststart', output],
    { stdio: ['pipe', 'inherit', 'inherit'] });
  ffmpeg.stdin.on('error', () => {});
  ffmpegClosed = new Promise(resolve => {
    ffmpeg.on('error', error => { ffmpegFailed = new Error('无法运行 ffmpeg：' + error.message); resolve(); });
    ffmpeg.on('close', code => { ffmpegFailed ||= code ? new Error('ffmpeg 编码失败，退出码 ' + code) : null; resolve(); });
  });
  console.log(`编码器：${encoder[1]}`);
} else {
  fs.mkdirSync(stills, { recursive: true });
}

// Frames finish out of order; ffmpeg receives them strictly in order.
const ready = new Map();
let written = 0, writing = Promise.resolve();
function deliver(index, data) {
  ready.set(index, data);
  writing = writing.then(async () => {
    while (ready.has(written) && !ffmpegFailed) {
      const chunk = ready.get(written);
      ready.delete(written);
      written++;
      if (!ffmpeg.stdin.write(chunk)) await Promise.race([new Promise(resolve => ffmpeg.stdin.once('drain', resolve)), ffmpegClosed]);
    }
  });
}

const pool = await Promise.all(Array.from({ length: browsers }, () => launch(chromium)));
let next = 0, done = 0, reported = 0;
async function worker(k) {
  const tab = await pool[k % browsers].newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  tab.on('pageerror', error => console.error('页面错误：' + error.message));
  await tab.goto(pathToFileURL(page).href);
  await tab.evaluate(() => document.fonts.ready);
  if (!(await tab.evaluate(() => typeof window.render === 'function'))) throw new Error('页面没有定义 window.render(t)');
  await tab.waitForTimeout(300);
  while (next < times.length) {
    if (ffmpegFailed) throw ffmpegFailed;
    const index = next++;
    // Bound the reorder buffer so memory stays flat on long videos.
    while (ffmpeg && index - written > workers * 8) {
      if (ffmpegFailed) throw ffmpegFailed;
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    await tab.evaluate(t => window.render(t), times[index]);
    const file = stills && path.join(stills, `t-${times[index].toFixed(2)}.jpg`);
    const data = await tab.screenshot({ type: 'jpeg', quality, ...(file ? { path: file } : {}) });
    if (ffmpeg) deliver(index, data);
    done++;
    if (!stills && done - reported >= Math.max(30, times.length / 10)) {
      reported = done;
      const seconds = (Date.now() - started) / 1000;
      console.log(`${done}/${times.length} 帧，${seconds.toFixed(0)} 秒，预计还要 ${(seconds / done * (times.length - done)).toFixed(0)} 秒`);
    }
  }
  await tab.close();
}

try {
  await Promise.all(Array.from({ length: workers }, (_, k) => worker(k)));
  await writing;
} finally {
  await Promise.all(pool.map(browser => browser.close().catch(() => {})));
  if (ffmpeg) {
    ffmpeg.stdin.end();
    await ffmpegClosed;
  }
}
if (ffmpegFailed) throw ffmpegFailed;
const seconds = ((Date.now() - started) / 1000).toFixed(1);
console.log(stills ? `已截 ${times.length} 帧到 ${stills}，${seconds} 秒`
  : `完成：${times.length} 帧，${seconds} 秒（${browsers} 个浏览器，${workers} 个页面） -> ${output}`);
