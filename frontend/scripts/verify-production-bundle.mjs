import { readdir, readFile, stat } from 'node:fs/promises';
import { basename, extname, relative, resolve } from 'node:path';

const distDirectory = process.argv[2] ?? 'dist';
const distRoot = resolve(process.cwd(), distDirectory);
const distName = basename(distRoot);
const isWorkspaceBundle = distName === 'dist-app';
const isBlackboxBundle = distName === 'dist-blackbox';
const expectedApiOrigin = (process.env.EXPECTED_API_ORIGIN ?? '').trim().replace(/\/+$/, '');
const forbidden = [
  { label: 'workspace de teste', pattern: /workspace-dev-test-klm/i },
  { label: 'usuário de teste', pattern: /user-dev-test-owner/i },
  { label: 'fixture de playbook', pattern: /fixture-playbook-v1/i },
  { label: 'origem local fixa', pattern: /http:\/\/127\.0\.0\.1:8010/i },
  { label: 'origem externa fixa', pattern: /https:\/\/api\.desburocratize\.com/i },
  { label: 'nome externo de atendimento', pattern: /chatwoot/i },
  { label: 'nome externo de colaboração', pattern: /rocket(?:[. _-]?chat)/i },
  { label: 'nome externo de automação', pattern: /chatter(?:[. _-]?mate)/i },
  { label: 'nome externo de CRM', pattern: /bottle(?:[. _-]?crm)/i },
  { label: 'topologia auxiliar', pattern: /sidecar/i },
  { label: 'alias público legado', pattern: /\/api\/v1\/oss-/i },
  { label: 'prefixo técnico legado', pattern: /(?:^|[^a-z0-9])oss[-_/][a-z0-9]/i },
  { label: 'nome técnico legado', pattern: /(?:^|[^a-z0-9])oss(?:[^a-z0-9]|$)/i },
  ...(isWorkspaceBundle
    ? [
        { label: 'painel administrativo no bundle do cliente', pattern: /PlatformDashboard/i },
        { label: 'shell Blackbox no bundle do cliente', pattern: /BlackboxShell/i },
        { label: 'home de funcionário no bundle do cliente', pattern: /BlackboxRoleHome/i },
      ]
    : []),
  ...(isBlackboxBundle
    ? [
        { label: 'conversas do cliente no bundle Blackbox', pattern: /ConversationsPage/i },
        { label: 'shell do workspace no bundle Blackbox', pattern: /KlmShell/i },
      ]
    : []),
];
const textualExtensions = new Set([
  '.css',
  '.html',
  '.js',
  '.json',
  '.map',
  '.mjs',
  '.svg',
  '.txt',
  '.webmanifest',
  '.xml',
]);

async function filesUnder(root) {
  const entries = await readdir(root, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) files.push(...await filesUnder(path));
    else files.push(path);
  }
  return files;
}

const indexPath = resolve(distRoot, 'index.html');
if (!(await stat(indexPath)).isFile()) {
  throw new Error(`Build de produção não contém ${distDirectory}/index.html.`);
}

async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (error?.code === 'ENOENT') return false;
    throw error;
  }
}

if (isBlackboxBundle) {
  const requiredBrandAssets = [
    'brand/meu-executivo/mark-blue.svg',
    'brand/meu-executivo/wordmark.svg',
    'logo-icon.png',
    'site.webmanifest',
  ];
  const missingBrandAssets = [];
  for (const asset of requiredBrandAssets) {
    if (!await exists(resolve(distRoot, asset))) missingBrandAssets.push(asset);
  }
  if (missingBrandAssets.length) {
    throw new Error(`Bundle Blackbox sem ativos obrigatórios da marca: ${missingBrandAssets.join(', ')}`);
  }
  for (const excludedDirectory of ['proposal-media', 'site-media']) {
    if (await exists(resolve(distRoot, excludedDirectory))) {
      throw new Error(`Bundle Blackbox contém mídia pública fora de escopo: ${excludedDirectory}`);
    }
  }
}

const offenders = [];
let expectedApiOriginFound = false;
for (const path of await filesUnder(distRoot)) {
  const bundlePath = relative(distRoot, path);
  const body = textualExtensions.has(extname(path).toLowerCase())
    ? await readFile(path, 'utf8')
    : null;
  for (const marker of forbidden) {
    if (marker.pattern.test(bundlePath)) offenders.push(`${bundlePath} (nome): ${marker.label}`);
    if (body !== null && marker.pattern.test(body)) {
      offenders.push(`${bundlePath} (conteúdo): ${marker.label}`);
    }
  }
  if (body !== null && expectedApiOrigin && body.includes(expectedApiOrigin)) {
    expectedApiOriginFound = true;
  }
}

if (offenders.length) {
  throw new Error(`Bundle de produção contém dados proibidos:\n${offenders.join('\n')}`);
}

if (expectedApiOrigin) {
  let parsedOrigin;
  try {
    parsedOrigin = new URL(expectedApiOrigin);
  } catch {
    throw new Error(`EXPECTED_API_ORIGIN inválida: ${expectedApiOrigin}`);
  }
  if (parsedOrigin.protocol !== 'https:' || parsedOrigin.origin !== expectedApiOrigin) {
    throw new Error('EXPECTED_API_ORIGIN deve ser uma origem HTTPS sem path, query ou fragmento.');
  }
  if (!expectedApiOriginFound) {
    throw new Error(
      `Bundle ${distDirectory} não contém a origem obrigatória da API: ${expectedApiOrigin}.`,
    );
  }

  const mutuallyExclusiveOrigin = expectedApiOrigin.includes('staging-api.meuexecutivo.com')
    ? 'https://api.meuexecutivo.com'
    : 'https://staging-api.meuexecutivo.com';
  for (const path of await filesUnder(distRoot)) {
    if (!textualExtensions.has(extname(path).toLowerCase())) continue;
    const body = await readFile(path, 'utf8');
    if (body.includes(mutuallyExclusiveOrigin)) {
      throw new Error(
        `Bundle ${distDirectory} mistura a origem esperada com ${mutuallyExclusiveOrigin}.`,
      );
    }
  }
}

console.log(
  `Bundle ${distDirectory} verificado: fronteira Meu Executivo, API ${expectedApiOrigin || 'same-origin'} e sem dados dev/test.`,
);
