import { defineConfig } from '@playwright/test';

const desktopOnly = /@desktop-only/;
const mobileOnly = /@mobile-only/;
const viewportOnly = /@(?:desktop|mobile)-only/;
const releaseBundle = process.env.KLM_E2E_PRODUCTION === '1';
const externalServer = process.env.KLM_E2E_EXTERNAL_SERVER === '1';
const frontendUrl = process.env.KLM_FRONTEND_URL
  ?? 'http://127.0.0.1:4173';
const blackboxUrl = process.env.KLM_BLACKBOX_URL ?? 'http://127.0.0.1:4174';
const apiUrl = process.env.KLM_API_URL ?? 'http://127.0.0.1:8010';
const frontendPort = new URL(frontendUrl).port || '4173';
const blackboxPort = new URL(blackboxUrl).port || '4174';
// Keep every Blackbox journey on the isolated internal bundle.  Commercial
// journeys live in their own file because they need a real disposable API and
// database, but must never be picked up by the tenant `/app` projects.
const blackboxSpec = /blackbox(?:-[^.]+)?\.spec\.ts/;
const responsiveSpec = /responsive(?:-[^.]+)?\.spec\.ts/;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  // The local E2E suite mutates and restores one shared SQLite workspace.
  // Running viewport projects concurrently makes those transactions race.
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [['line']],
  use: {
    baseURL: frontendUrl,
    screenshot: 'only-on-failure',
    // The 135-route OSS matrix is intentionally exhaustive; opt into tracing
    // only for diagnosis so routine QA does not write multi-gigabyte snapshots.
    trace: process.env.KLM_E2E_TRACE === '1' ? 'retain-on-failure' : 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'desktop-1440',
      testIgnore: new RegExp(`${blackboxSpec.source}|${responsiveSpec.source}`),
      grepInvert: mobileOnly,
      use: { viewport: { width: 1440, height: 1000 } },
    },
    {
      name: 'mobile-393',
      testIgnore: new RegExp(`${blackboxSpec.source}|${responsiveSpec.source}`),
      grepInvert: desktopOnly,
      use: {
        viewport: { width: 393, height: 852 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: 'tablet-834',
      testIgnore: new RegExp(`${blackboxSpec.source}|${responsiveSpec.source}`),
      grepInvert: viewportOnly,
      use: {
        viewport: { width: 834, height: 1194 },
        hasTouch: true,
      },
    },
    {
      name: 'blackbox-desktop-1440',
      testMatch: blackboxSpec,
      use: {
        baseURL: blackboxUrl,
        viewport: { width: 1440, height: 1000 },
      },
    },
    {
      // The responsive spec also runs at desktop width as a non-regression
      // guard for all surfaces outside the Financeiro redesign.
      name: 'desktop-responsive-1440',
      testMatch: responsiveSpec,
      grepInvert: /@desktop-freeze/,
      use: { viewport: { width: 1440, height: 1000 } },
    },
    {
      name: 'desktop-freeze-1440',
      testMatch: responsiveSpec,
      grep: /@desktop-freeze/,
      use: { viewport: { width: 1440, height: 1000 } },
    },
    // Focused responsive/accessibility probes.  Keeping these projects scoped
    // to responsive specs avoids multiplying the exhaustive route matrix.
    ...[
      ['mobile-360', 360, 800, true, true],
      ['mobile-responsive-393', 393, 852, true, true],
      ['mobile-430', 430, 932, true, true],
      ['tablet-768', 768, 1024, true, false],
      ['tablet-responsive-834', 834, 1194, true, false],
      ['tablet-1024', 1024, 1366, true, false],
      ['tablet-1194', 1194, 834, true, false],
    ].map(([name, width, height, hasTouch, isMobile]) => ({
      name: name as string,
      testMatch: responsiveSpec,
      grepInvert: /@desktop-freeze/,
      use: {
        viewport: { width: width as number, height: height as number },
        hasTouch: hasTouch as boolean,
        isMobile: isMobile as boolean,
      },
    })),
  ],
  webServer: externalServer ? undefined : [
    {
      command: releaseBundle
        ? `npm run build:app && npm run preview -- --host 127.0.0.1 --port ${frontendPort} --strictPort --outDir dist-app`
        : 'npm run dev:app',
      url: `${frontendUrl}/app`,
      reuseExistingServer: !releaseBundle,
      timeout: releaseBundle ? 90_000 : 30_000,
      env: {
        ...process.env,
        // Release-style E2E bundles must remain deployable artifacts.  The
        // preview server already proxies /api to the disposable backend, so
        // embedding the loopback API origin would both diverge from the
        // same-origin fallback and correctly fail the production-bundle gate.
        ...(releaseBundle ? {} : { VITE_API_URL: process.env.VITE_API_URL ?? apiUrl }),
      },
    },
    {
      command: releaseBundle
        ? `npm run build:blackbox && npm run preview -- --host 127.0.0.1 --port ${blackboxPort} --strictPort --outDir dist-blackbox`
        : 'npm run dev:blackbox',
      url: `${blackboxUrl}/blackbox`,
      reuseExistingServer: !releaseBundle,
      timeout: releaseBundle ? 90_000 : 30_000,
      env: {
        ...process.env,
        ...(releaseBundle ? {} : { VITE_API_URL: process.env.VITE_API_URL ?? apiUrl }),
      },
    },
  ],
});
