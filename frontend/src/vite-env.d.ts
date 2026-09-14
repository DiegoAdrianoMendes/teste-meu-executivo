/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_SURFACE?: 'workspace' | 'platform';
  readonly VITE_API_URL?: string;
  readonly VITE_KLM_RUNTIME_MODE?: string;
  readonly VITE_META_GRAPH_API_VERSION?: string;
  /** URL do app das contas (só o build da Blackbox usa: link "Esqueceu a senha?", V43). */
  readonly VITE_WORKSPACE_APP_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
