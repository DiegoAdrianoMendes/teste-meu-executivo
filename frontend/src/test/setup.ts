import "@testing-library/jest-dom";
import { configure } from "@testing-library/dom";

// O `waitFor` desiste em 1s por padrão. Com 138 arquivos de teste rodando em
// paralelo, telas pesadas (Grupos, Campanhas) levam mais que isso só para
// assentar, e falhavam na suíte completa enquanto passavam isoladas — pior
// ainda no runner do CI, que tem menos núcleos que uma máquina de trabalho.
// Esperar mais não enfraquece asserção nenhuma: a condição continua a mesma e
// o teste ainda falha se ela nunca for satisfeita. Só deixa de confundir
// lentidão com defeito.
configure({ asyncUtilTimeout: 5000 });

const TEST_ELEMENT_WIDTH = 1024;
const TEST_ELEMENT_HEIGHT = 400;

function testElementRect(element: Element): DOMRectReadOnly {
  const width = Number.parseFloat((element as HTMLElement).style.width) || TEST_ELEMENT_WIDTH;
  const height = Number.parseFloat((element as HTMLElement).style.height) || TEST_ELEMENT_HEIGHT;
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON: () => ({ width, height }),
  };
}

// jsdom does not perform layout. Components such as Recharts legitimately
// wait for ResizeObserver before rendering, so a no-op observer makes every
// chart look 0 x 0 and hides layout regressions behind console noise. Keep the
// harness deterministic by reporting a real test viewport instead.
class ResizeObserverMock implements ResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    const contentRect = testElementRect(target);
    const observedSize = {
      inlineSize: contentRect.width,
      blockSize: contentRect.height,
    };
    this.callback(
      [
        {
          target,
          contentRect,
          borderBoxSize: [observedSize],
          contentBoxSize: [observedSize],
          devicePixelContentBoxSize: [observedSize],
        },
      ],
      this,
    );
  }
  unobserve() {}
  disconnect() {}
}

if (typeof window !== "undefined") {
  Object.defineProperty(window, "ResizeObserver", {
    writable: true,
    value: ResizeObserverMock,
  });

  Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
    writable: true,
    value: () => {},
  });

  Object.defineProperty(window.HTMLElement.prototype, "getBoundingClientRect", {
    configurable: true,
    writable: true,
    value: function getBoundingClientRect(this: HTMLElement) {
      return testElementRect(this);
    },
  });

  Object.defineProperty(window, "scrollTo", {
    writable: true,
    value: () => {},
  });

  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => {},
    }),
  });
}
