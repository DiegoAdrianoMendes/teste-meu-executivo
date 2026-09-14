#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(frontendRoot, '..');
const defaultOutputDir = path.join(root, '.local/qa-evidence/saas-v5-p5/visual');
const routes = [
  ['/app', 'inicio'],
  ['/app/conversas', 'conversas'],
  ['/app/funil', 'funil'],
  ['/app/kpis', 'kpis'],
  ['/app/flows', 'flows'],
  ['/app/agenda', 'agenda'],
  ['/app/ferramentas', 'ferramentas'],
  ['/app/configuracoes', 'configuracoes'],
];
const profiles = [
  { name: 'desktop-1440', viewport: { width: 1440, height: 1000 }, isMobile: false },
  { name: 'mobile-393', viewport: { width: 393, height: 852 }, isMobile: true },
];

function parseArguments(argv) {
  const values = {
    baseUrl: 'http://127.0.0.1:4173',
    outputDir: defaultOutputDir,
    manifest: path.join(defaultOutputDir, 'visual-evidence.json'),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!['--base-url', '--output-dir', '--manifest'].includes(flag) || !value) {
      throw new Error(`Unsupported or incomplete argument: ${flag}`);
    }
    if (flag === '--base-url') values.baseUrl = value.replace(/\/$/, '');
    if (flag === '--output-dir') values.outputDir = path.resolve(root, value);
    if (flag === '--manifest') values.manifest = path.resolve(root, value);
    index += 1;
  }
  const parsed = new URL(values.baseUrl);
  if (!['127.0.0.1', 'localhost', '::1'].includes(parsed.hostname)) {
    throw new Error('Visual evidence base URL must be loopback-only.');
  }
  return values;
}

function relativeToRoot(filePath) {
  const relative = path.relative(root, filePath);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`Evidence path escapes the repository: ${filePath}`);
  }
  return relative.split(path.sep).join('/');
}

function sha256(payload) {
  return createHash('sha256').update(payload).digest('hex');
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

function assertEvidencePath(candidate, parent, label) {
  const relative = path.relative(parent, candidate);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`${label} must remain below ${relativeToRoot(parent)}.`);
  }
}

async function fileEvidence(filePath, logicalPath, kind, metadata = {}) {
  const payload = await readFile(filePath);
  return {
    kind,
    path: relativeToRoot(logicalPath),
    bytes: payload.length,
    sha256: sha256(payload),
    ...metadata,
  };
}

async function publishEvidence(stagingDir, outputDir, backupDir) {
  await rm(backupDir, { recursive: true, force: true });
  let previousMoved = false;
  try {
    try {
      await rename(outputDir, backupDir);
      previousMoved = true;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    await rename(stagingDir, outputDir);
    await rm(backupDir, { recursive: true, force: true });
  } catch (error) {
    if (previousMoved) {
      await rm(outputDir, { recursive: true, force: true });
      await rename(backupDir, outputDir);
    }
    throw error;
  }
}

function isLoopbackUrl(rawUrl) {
  const parsed = new URL(rawUrl);
  return ['data:', 'blob:'].includes(parsed.protocol)
    || ['127.0.0.1', 'localhost', '::1'].includes(parsed.hostname);
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const evidenceRoot = path.join(root, '.local/qa-evidence/saas-v5-p5');
  assertEvidencePath(options.outputDir, evidenceRoot, 'Visual output directory');
  assertEvidencePath(options.manifest, options.outputDir, 'Visual manifest');
  const stagingDir = `${options.outputDir}.staging-${process.pid}`;
  const backupDir = `${options.outputDir}.backup-${process.pid}`;
  assertEvidencePath(stagingDir, evidenceRoot, 'Visual staging directory');
  assertEvidencePath(backupDir, evidenceRoot, 'Visual backup directory');
  await rm(stagingDir, { recursive: true, force: true });
  await mkdir(stagingDir, { recursive: true });
  const manifestRelativePath = path.relative(options.outputDir, options.manifest);
  const stagingManifest = path.join(stagingDir, manifestRelativePath);
  await mkdir(path.dirname(stagingManifest), { recursive: true });
  const recordingDir = path.join(stagingDir, 'recordings');
  await mkdir(recordingDir, { recursive: true });

  const externalRequests = new Set();
  const consoleErrors = [];
  const pageErrors = [];
  const localRequestFailures = [];
  const serverErrors = [];
  const screenshots = [];
  const videos = [];
  const profileResults = [];
  let browser;
  let published = false;
  try {
    browser = await chromium.launch({ headless: true });
    const browserVersion = browser.version();
    for (const profile of profiles) {
      const context = await browser.newContext({
        viewport: profile.viewport,
        isMobile: profile.isMobile,
        hasTouch: profile.isMobile,
        reducedMotion: 'reduce',
        recordVideo: { dir: recordingDir, size: profile.viewport },
      });
      await context.route('**/*', async (route) => {
        if (isLoopbackUrl(route.request().url())) {
          await route.continue();
          return;
        }
        externalRequests.add(route.request().url());
        await route.abort('blockedbyclient');
      });
      const page = await context.newPage();
      const video = page.video();
      page.on('request', (request) => {
        if (!isLoopbackUrl(request.url())) externalRequests.add(request.url());
      });
      page.on('requestfailed', (request) => {
        if (!isLoopbackUrl(request.url())) return;
        localRequestFailures.push({
          profile: profile.name,
          method: request.method(),
          resourceType: request.resourceType(),
          url: request.url(),
          error: request.failure()?.errorText ?? 'unknown',
        });
      });
      page.on('websocket', (socket) => {
        if (!isLoopbackUrl(socket.url())) externalRequests.add(socket.url());
      });
      page.on('console', (message) => {
        if (message.type() !== 'error' || message.text().startsWith('Failed to load resource:')) {
          return;
        }
        consoleErrors.push({ profile: profile.name, message: message.text() });
      });
      page.on('pageerror', (error) => {
        pageErrors.push({ profile: profile.name, message: error.message });
      });
      page.on('response', (response) => {
        if (response.status() >= 500) {
          serverErrors.push({
            profile: profile.name,
            status: response.status(),
            url: response.url(),
          });
        }
      });

      const visited = [];
      for (const [route, slug] of routes) {
        const response = await page.goto(`${options.baseUrl}${route}`, {
          waitUntil: 'domcontentloaded',
          timeout: 30_000,
        });
        if (!response || response.status() >= 400) {
          throw new Error(`${profile.name} ${route} returned HTTP ${response?.status() ?? 0}`);
        }
        const mainSurface = page.locator('main').first();
        try {
          await mainSurface.waitFor({ state: 'visible', timeout: 20_000 });
        } catch (error) {
          throw new Error(`${profile.name} ${route} has no visible main surface: ${error.message}`);
        }
        const visibleText = (await mainSurface.innerText()).trim();
        if (visibleText.length < 20) {
          throw new Error(`${profile.name} ${route} rendered too little visible content.`);
        }
        try {
          await page.waitForFunction(() => {
            const main = document.querySelector('main');
            const text = main?.textContent ?? '';
            return !/(?:\bCarregando\b|\bConsultando\b|\bVerificando\b|\bLendo\b|Aguardando a API local)/i.test(text);
          }, undefined, { timeout: 8_000 });
        } catch (error) {
          throw new Error(`${profile.name} ${route} did not finish loading: ${error.message}`);
        }
        await page.addStyleTag({
          content: '*,*::before,*::after{animation:none!important;transition:none!important}',
        });
        await page.waitForTimeout(150);
        const screenshotName = `${profile.name}-${slug}.png`;
        const screenshotPath = path.join(stagingDir, screenshotName);
        const logicalScreenshotPath = path.join(options.outputDir, screenshotName);
        await page.screenshot({
          path: screenshotPath,
          fullPage: false,
          animations: 'disabled',
        });
        screenshots.push(await fileEvidence(screenshotPath, logicalScreenshotPath, 'screenshot', {
          profile: profile.name,
          route,
          viewport: profile.viewport,
        }));
        visited.push({ route, visibleTextLength: visibleText.length });
      }

      await context.close();
      if (!video) throw new Error(`Playwright did not create a video for ${profile.name}.`);
      const videoName = `${profile.name}.webm`;
      const videoPath = path.join(stagingDir, videoName);
      const logicalVideoPath = path.join(options.outputDir, videoName);
      await video.saveAs(videoPath);
      videos.push(await fileEvidence(videoPath, logicalVideoPath, 'video', {
        profile: profile.name,
        viewport: profile.viewport,
      }));
      profileResults.push({
        name: profile.name,
        viewport: profile.viewport,
        routesVisited: visited.map((item) => item.route),
        visibleTextLengths: Object.fromEntries(
          visited.map((item) => [item.route, item.visibleTextLength]),
        ),
        screenshotCount: visited.length,
        videoRecorded: true,
      });
    }
    await browser.close();
    browser = undefined;

    const passed = externalRequests.size === 0
      && localRequestFailures.length === 0
      && consoleErrors.length === 0
      && pageErrors.length === 0
      && serverErrors.length === 0
      && screenshots.length === profiles.length * routes.length
      && videos.length === profiles.length;
    const manifest = {
      schemaVersion: '1.0',
      kind: 'klm-saas-v5-p5-visual-evidence',
      generatedAt: new Date().toISOString(),
      status: passed ? 'passed' : 'failed',
      localOnly: true,
      baseUrl: options.baseUrl,
      browser: { engine: 'chromium', version: browserVersion },
      routes: routes.map(([route]) => route),
      profiles: profileResults,
      screenshotCount: screenshots.length,
      videoCount: videos.length,
      screenshots,
      videos,
      runtimeFailures: {
        externalNetworkRequests: externalRequests.size,
        externalRequestUrls: [...externalRequests].slice(0, 25),
        localRequestFailures,
        consoleErrors,
        pageErrors,
        serverErrors,
      },
      claims: {
        localVisualEvidenceVerified: passed,
        productionVerified: false,
        externalProviderVerified: false,
      },
    };
    manifest.manifestDigest = sha256(canonicalJson(manifest));
    await writeFile(stagingManifest, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
    if (passed) {
      await publishEvidence(stagingDir, options.outputDir, backupDir);
      published = true;
    }
    process.stdout.write(`${JSON.stringify({
      manifest: published ? relativeToRoot(options.manifest) : null,
      published,
      screenshots: screenshots.length,
      status: manifest.status,
      videos: videos.length,
    })}\n`);
    if (!passed) process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (!published) await rm(stagingDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  process.stderr.write(`P5_VISUAL_EVIDENCE_REFUSED: ${error.message}\n`);
  process.exitCode = 2;
});
