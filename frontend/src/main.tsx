import { createRoot } from "react-dom/client";
import "@fontsource-variable/lexend";
import "@fontsource-variable/geist";
import "@fontsource-variable/jetbrains-mono";
import "./index.css";
import { isLocalSimulation } from "./klm/runtime.ts";
import { recoverAccessToken } from "./services/auth.ts";

async function start() {
  if (isLocalSimulation) {
    try {
      await recoverAccessToken();
    } catch {
      // O SPA ainda renderiza a tela de conectividade; nenhuma fixture substitui o backend.
    }
  }
  const { default: App } = await import("@surface-app");
  createRoot(document.getElementById("root")!).render(<App />);
}

void start();
