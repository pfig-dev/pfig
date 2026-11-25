import { createContext, useContext } from "react";

/** Which edges to emphasize in the graph (glow). */
export type EdgeHoverHighlight =
  | { kind: "source"; nodeId: string }
  | { kind: "sink"; nodeId: string; port: string }
  | null;

export const GraphHighlightContext = createContext<{
  setHighlight: (h: EdgeHoverHighlight) => void;
  /** Endpoints of every currently glowing edge (hover on edge, source zone, or sink row). */
  glowingNodeIds: ReadonlySet<string>;
} | null>(null);

type EdgeEnd = {
  id: string;
  source: string;
  target: string;
  targetHandle?: string | null;
};

export function computeGlowingNodeIds(
  edges: readonly EdgeEnd[],
  hl: EdgeHoverHighlight,
  hoveredEdgeId: string | null
): ReadonlySet<string> {
  const s = new Set<string>();
  if (hoveredEdgeId) {
    const e = edges.find((x) => x.id === hoveredEdgeId);
    if (e) {
      s.add(e.source);
      s.add(e.target);
    }
  }
  if (hl?.kind === "source") {
    s.add(hl.nodeId);
    for (const e of edges) {
      if (e.source === hl.nodeId) s.add(e.target);
    }
  }
  if (hl?.kind === "sink") {
    s.add(hl.nodeId);
    for (const e of edges) {
      const port = portFromTargetHandle(e.targetHandle);
      if (e.target === hl.nodeId && port === hl.port) s.add(e.source);
    }
  }
  return s;
}

export function useGraphHighlight() {
  return useContext(GraphHighlightContext);
}

/** targetHandle is `kw-<targetPort>` from the view model. */
export function portFromTargetHandle(handle: string | null | undefined): string | null {
  if (!handle || !handle.startsWith("kw-")) return null;
  return handle.slice("kw-".length);
}

export function edgeMatchesHighlight(
  source: string,
  target: string,
  targetHandle: string | null | undefined,
  hl: EdgeHoverHighlight
): boolean {
  if (!hl) return false;
  if (hl.kind === "source") return source === hl.nodeId;
  const port = portFromTargetHandle(targetHandle);
  return hl.kind === "sink" && target === hl.nodeId && port === hl.port;
}
