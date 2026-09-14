import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react-swc";
import fs from "node:fs";
import path from "path";
import { componentTagger } from "lovable-tagger";

function publicAssetsPlugin(appSurface: "workspace" | "platform"): Plugin {
  const publicRoot = path.resolve(__dirname, "./public");
  const excludedDirectories = new Set(
    appSurface === "platform"
      ? ["proposal-media", "site-media"]
      : ["site-media"],
  );

  return {
    name: `${appSurface}-public-assets`,
    apply: "build" as const,
    buildStart() {
      const visit = (directory: string) => {
        for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
          const absolutePath = path.join(directory, entry.name);
          const relativePath = path.relative(publicRoot, absolutePath);
          const [topLevelDirectory] = relativePath.split(path.sep);

          if (excludedDirectories.has(topLevelDirectory)) {
            continue;
          }
          if (entry.isDirectory()) {
            visit(absolutePath);
            continue;
          }
          if (!entry.isFile()) {
            continue;
          }

          this.emitFile({
            type: "asset",
            fileName: relativePath.split(path.sep).join("/"),
            source: fs.readFileSync(absolutePath),
          });
        }
      };

      visit(publicRoot);
    },
  };
}

const localContentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self' 'unsafe-inline' https://connect.facebook.net",
  "script-src-attr 'none'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "connect-src 'self' https: wss: http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*",
  "frame-src https://*.facebook.com",
  "media-src 'self' blob: https:",
  "worker-src 'self' blob:",
  "manifest-src 'self'",
].join("; ");

const localSecurityHeaders = {
  "Cache-Control": "no-store",
  "Content-Security-Policy": localContentSecurityPolicy,
  "Permissions-Policy": "accelerometer=(), autoplay=(), camera=(), clipboard-read=(), clipboard-write=(self), display-capture=(), encrypted-media=(), fullscreen=(self), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), picture-in-picture=(), usb=()",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

const localApiTarget = process.env.KLM_DEV_API_TARGET ?? "http://127.0.0.1:8010";

const localApiProxy = {
  "/api": {
    target: localApiTarget,
    changeOrigin: false,
    ws: true,
  },
};

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const appSurface = process.env.VITE_APP_SURFACE === "platform"
    ? "platform"
    : "workspace";

  return {
  cacheDir: `node_modules/.vite-${appSurface}`,
  // O site público tem seu próprio deploy. Evitar que App/Blackbox dependam
  // de mídia institucional (inclusive arquivos iCloud dataless) no build.
  publicDir: mode === "development" ? "public" : false,
  server: {
    host: "::",
    port: 8080,
    headers: localSecurityHeaders,
    hmr: {
      overlay: false,
    },
    proxy: localApiProxy,
  },
  preview: {
    headers: localSecurityHeaders,
    proxy: localApiProxy,
  },
  plugins: [
    react(),
    mode !== "development" && publicAssetsPlugin(appSurface),
    mode === "development" && componentTagger(),
  ].filter(Boolean),
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes('/node_modules/@jsonforms/')
            || id.includes('/node_modules/ajv/')
          ) return 'jsonforms-runtime';
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: [
      {
        find: "@surface-app",
        replacement: path.resolve(
          __dirname,
          appSurface === "platform"
            ? "./src/BlackboxApp.tsx"
            : "./src/App.tsx",
        ),
      },
      {
        find: /^react-remove-scroll$/,
        replacement: path.resolve(__dirname, "./src/vendor/react-remove-scroll.tsx"),
      },
      {
        find: "@klm/frontend-contract-fixtures",
        replacement: path.resolve(
          __dirname,
          mode === "production"
            ? "./src/fixtures/production/frontend-contracts.ts"
            : "./src/fixtures/dev-test/frontend-contracts.ts",
        ),
      },
      { find: "@", replacement: path.resolve(__dirname, "./src") },
    ],
    dedupe: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime"],
  },
  };
});
