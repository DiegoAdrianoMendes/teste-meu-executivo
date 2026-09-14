import { readFileSync, readdirSync } from 'node:fs';
import { extname, join, relative } from 'node:path';

const sourceRoot = new URL('../src/', import.meta.url);
const operationalRoots = ['pages', 'components/platform', 'support-mode'];
const internalEntity = '(?:conta|caso|tarefa|usu[aá]rio|funcion[aá]rio|ticket|lead|conversa|incidente|workspace|release)';
const forbidden = [
  new RegExp(`\\bUUID(?:s)?\\b.{0,48}\\b${internalEntity}\\b`, 'giu'),
  new RegExp(`\\bID (?:da|do|de) ${internalEntity}\\b`, 'giu'),
  new RegExp(`\\b${internalEntity} ID\\b`, 'giu'),
];

function filesUnder(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return filesUnder(path);
    return ['.ts', '.tsx'].includes(extname(entry.name)) ? [path] : [];
  });
}

const violations = [];
for (const root of operationalRoots) {
  const absoluteRoot = new URL(`${root}/`, sourceRoot).pathname;
  for (const file of filesUnder(absoluteRoot)) {
    const lines = readFileSync(file, 'utf8').split(/\r?\n/);
    lines.forEach((line, index) => {
      if (line.includes('data-external-id') || line.includes('data-technical-id')) return;
      for (const pattern of forbidden) {
        pattern.lastIndex = 0;
        if (!pattern.test(line)) continue;
        violations.push(`${relative(sourceRoot.pathname, file)}:${index + 1}: ${line.trim()}`);
        break;
      }
    });
  }
}

if (violations.length) {
  console.error('Campos ou textos operacionais ainda solicitam identificadores internos:');
  violations.forEach((violation) => console.error(`- ${violation}`));
  console.error('Use um seletor autorizado. IDs externos devem ser marcados com data-external-id; detalhes técnicos readonly com data-technical-id.');
  process.exit(1);
}

console.log('Blackbox UX: nenhum identificador interno solicitado ao operador.');
