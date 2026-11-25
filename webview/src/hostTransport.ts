import type { GraphViewModel, RangeJson } from "./types";
import type { ExportPngOpts, GraphExportColorMode } from "./graphPngExport";

type GraphWebviewState = {
  showValueNodes?: boolean;
  foldValueNodes?: boolean;
};

type HostMessage =
  | { type: "exportPng"; exportDpi?: number; exportMaxEdgePx?: number; colorMode?: string }
  | { type: "graphColorMode"; colorMode?: string }
  | { type: "graphData"; payload: GraphViewModel; colorMode?: string };

type VscodeApi = {
  postMessage: (msg: unknown) => void;
  getState: () => unknown;
  setState: (state: unknown) => void;
};

export type GraphHostTransport = {
  getState: () => GraphWebviewState | undefined;
  setState: (state: GraphWebviewState) => void;
  postMessage: (msg: unknown) => void;
  ready: () => void;
  requestGraph: (mode: "reachable" | "all") => void;
  canRevealSource: () => boolean;
  reveal: (uri: string, range: RangeJson) => void;
  onMessage: (handler: (message: HostMessage) => void) => () => void;
  defaultColorMode: GraphExportColorMode;
};

declare global {
  interface Window {
    acquireVsCodeApi?: () => VscodeApi;
  }
}

const STATE_KEY = "pfig.graph.state";

function graphApiUrl(mode?: "reachable" | "all"): string {
  const url = new URL("/api/graph", window.location.href);
  if (mode) {
    url.searchParams.set("mode", mode);
  }
  return url.toString();
}

function browserColorMode(): GraphExportColorMode {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function downloadPng(base64: string): void {
  const a = document.createElement("a");
  a.href = `data:image/png;base64,${base64}`;
  a.download = "pfig-graph.png";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function createBrowserTransport(): GraphHostTransport {
  let activeHandler: ((message: HostMessage) => void) | null = null;
  let supportsSourceReveal = false;

  const emitGraph = async (mode?: "reachable" | "all") => {
    const fallbackMode = mode ?? "reachable";
    try {
      const res = await fetch(graphApiUrl(mode), { cache: "no-store" });
      const payload = (await res.json()) as GraphViewModel;
      supportsSourceReveal = payload.supportsSourceReveal === true;
      activeHandler?.({ type: "graphData", payload, colorMode: browserColorMode() });
    } catch (e) {
      activeHandler?.({
        type: "graphData",
        payload: {
          ok: false,
          error: e instanceof Error ? e.message : String(e),
          nodes: [],
          edges: [],
          mode: fallbackMode,
          reachableUnavailable: true,
          entrypoint: null,
        },
        colorMode: browserColorMode(),
      });
    }
  };

  return {
    defaultColorMode: browserColorMode(),
    getState: () => {
      try {
        const raw = window.localStorage.getItem(STATE_KEY);
        return raw ? (JSON.parse(raw) as GraphWebviewState) : undefined;
      } catch {
        return undefined;
      }
    },
    setState: (state) => {
      try {
        window.localStorage.setItem(STATE_KEY, JSON.stringify(state));
      } catch {
        // localStorage may be unavailable in hardened browser contexts.
      }
    },
    postMessage: (msg: unknown) => {
      if (typeof msg !== "object" || msg === null || !("type" in msg)) {
        return;
      }
      const m = msg as { type?: string; base64?: string; message?: string };
      if (m.type === "exportPngResult" && typeof m.base64 === "string") {
        downloadPng(m.base64);
      } else if (m.type === "exportPngError" && typeof m.message === "string") {
        window.alert(m.message);
      }
    },
    ready: () => {
      void emitGraph();
    },
    requestGraph: (mode) => {
      void emitGraph(mode);
    },
    canRevealSource: () => supportsSourceReveal,
    reveal: (uri, range) => {
      if (!supportsSourceReveal) return;
      const url = new URL("/api/reveal", window.location.href);
      url.searchParams.set("uri", uri);
      url.searchParams.set("line", String(range.start.line));
      url.searchParams.set("character", String(range.start.character));
      void fetch(url.toString(), { redirect: "manual" }).then((res) => {
        if (res.type === "opaqueredirect" || res.status >= 300) {
          window.open(url.toString(), "_blank", "noopener,noreferrer");
        }
      });
    },
    onMessage: (handler) => {
      activeHandler = handler;
      return () => {
        activeHandler = null;
      };
    },
  };
}

function createVscodeTransport(vscode: VscodeApi): GraphHostTransport {
  return {
    defaultColorMode: "dark",
    getState: () => vscode.getState() as GraphWebviewState | undefined,
    setState: (state) => vscode.setState(state),
    postMessage: (msg) => vscode.postMessage(msg),
    ready: () => vscode.postMessage({ type: "ready" }),
    requestGraph: (mode) => vscode.postMessage({ type: "setMode", mode }),
    canRevealSource: () => true,
    reveal: (uri, range) => vscode.postMessage({ type: "reveal", uri, range }),
    onMessage: (handler) => {
      const listener = (event: MessageEvent) => handler(event.data as HostMessage);
      window.addEventListener("message", listener);
      return () => window.removeEventListener("message", listener);
    },
  };
}

export function createGraphHostTransport(): GraphHostTransport {
  if (typeof window.acquireVsCodeApi === "function") {
    return createVscodeTransport(window.acquireVsCodeApi());
  }
  return createBrowserTransport();
}

export function defaultExportOptions(colorMode: GraphExportColorMode): ExportPngOpts {
  return {
    exportDpi: 300,
    exportMaxEdgePx: 8192,
    colorMode,
  };
}
