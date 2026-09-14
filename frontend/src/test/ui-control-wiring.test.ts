import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

import { describe, expect, it } from 'vitest';

const sourceRoot = path.resolve(process.cwd(), 'src');
const skippedDirectories = new Set(['test', 'ui']);
const triggerNames = new Set([
  'AccordionTrigger',
  'AlertDialogTrigger',
  'CollapsibleTrigger',
  'DialogTrigger',
  'DropdownMenuTrigger',
  'HoverCardTrigger',
  'PopoverTrigger',
  'SheetTrigger',
  'TooltipTrigger',
]);

function sourceFiles(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const item = path.join(directory, entry.name);
    if (entry.isDirectory()) return skippedDirectories.has(entry.name) ? [] : sourceFiles(item);
    return entry.isFile() && item.endsWith('.tsx') ? [item] : [];
  });
}

function tagName(node: ts.JsxTagNameExpression): string {
  return node.getText().split('.').at(-1) ?? node.getText();
}

function isInsideTrigger(node: ts.Node): boolean {
  let current: ts.Node | undefined = node.parent;
  while (current) {
    if (ts.isJsxElement(current) && triggerNames.has(tagName(current.openingElement.tagName))) return true;
    current = current.parent;
  }
  return false;
}

describe('KLM interactive control wiring across production surfaces', () => {
  it('does not ship an enabled button without an action contract', () => {
    const unwired: string[] = [];

    for (const file of sourceFiles(sourceRoot)) {
      const contents = fs.readFileSync(file, 'utf8');
      const source = ts.createSourceFile(file, contents, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

      const visit = (node: ts.Node) => {
        const opening = ts.isJsxElement(node)
          ? node.openingElement
          : ts.isJsxSelfClosingElement(node)
            ? node
            : null;
        if (opening && ['Button', 'button'].includes(tagName(opening.tagName))) {
          const attributes = opening.attributes.properties;
          const names = attributes
            .filter(ts.isJsxAttribute)
            .map((attribute) => attribute.name.getText());
          const hasEvent = names.some((name) => /^on[A-Z]/.test(name));
          const hasSpread = attributes.some(ts.isJsxSpreadAttribute);
          const delegated = names.some((name) => ['asChild', 'disabled', 'form'].includes(name))
            || attributes.some((attribute) => ts.isJsxAttribute(attribute)
              && attribute.name.getText() === 'type'
              && attribute.initializer?.getText().includes('submit'))
            || isInsideTrigger(opening);

          if (!hasEvent && !hasSpread && !delegated) {
            const position = source.getLineAndCharacterOfPosition(opening.getStart(source));
            unwired.push(`${path.relative(process.cwd(), file)}:${position.line + 1}`);
          }
        }
        ts.forEachChild(node, visit);
      };

      visit(source);
    }

    expect(unwired, `Controles sem ação: ${unwired.join(', ')}`).toEqual([]);
  });
});
