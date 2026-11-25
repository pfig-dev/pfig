import type { GraphNodeVM } from "./types";

/** Visual buckets for graph cards (aligns with pfig view-model kinds). */
export type NodeVisualKind =
  | "provider"
  | "collection"
  | "value"
  | "alias"
  | "fresh_instance"
  | "external";

export interface NodePalette {
  visualKind: NodeVisualKind;
  /** Primary border (full card). */
  border: string;
  /** Card fill. */
  background: string;
  /** Optional left accent bar; same as border if unused. */
  accent: string;
  sinkHandle: string;
  sourceHandle: string;
  /** Card outline style (externals use dotted but share value colors). */
  borderStyle: "solid" | "dashed" | "dotted";
}

const COLLECTION: NodePalette = {
  visualKind: "collection",
  border: "#7c5cbf",
  background: "rgba(124, 92, 191, 0.14)",
  accent: "#a78bfa",
  sinkHandle: "#b49dfc",
  sourceHandle: "#8b6ae8",
  borderStyle: "solid",
};

const PROVIDER: NodePalette = {
  visualKind: "provider",
  border: "#2a9d8f",
  background: "rgba(42, 157, 143, 0.12)",
  accent: "#3dd4c3",
  sinkHandle: "#4fd1c5",
  sourceHandle: "#238b80",
  borderStyle: "solid",
};

const FRESH_INSTANCE: NodePalette = {
  ...PROVIDER,
  visualKind: "fresh_instance",
  borderStyle: "dashed",
};

const VALUE: NodePalette = {
  visualKind: "value",
  border: "#3d6ea8",
  background: "rgba(61, 110, 168, 0.12)",
  accent: "#6b9eff",
  sinkHandle: "#6b9eff",
  sourceHandle: "#4f83d9",
  borderStyle: "solid",
};

/** Provider/alias refs (@other) — distinct from literal values. */
const ALIAS: NodePalette = {
  visualKind: "alias",
  border: "#9d6b2e",
  background: "rgba(157, 107, 46, 0.14)",
  accent: "#d4a15c",
  sinkHandle: "#e0b070",
  sourceHandle: "#8b5a28",
  borderStyle: "solid",
};

/** Same fill as value nodes; dotted border marks injected externals. */
const EXTERNAL_AS_VALUE: NodePalette = {
  ...VALUE,
  visualKind: "external",
  borderStyle: "dotted",
};

export function resolveNodePalette(n: GraphNodeVM): NodePalette {
  const isExternal = Boolean(n.flags?.isExternal || n.kind === "external");
  if (isExternal) {
    return EXTERNAL_AS_VALUE;
  }
  if (n.collection?.slots?.length) {
    return COLLECTION;
  }
  if (n.provider) {
    return PROVIDER;
  }
  if (n.freshInstance) {
    return FRESH_INSTANCE;
  }
  if (n.kind === "alias") {
    return ALIAS;
  }
  return VALUE;
}

/** Default edge stroke opacity (semi-transparent over cards and background). */
const EDGE_STROKE_ALPHA = 0.55;

/** Edge stroke colors (shared vs fresh); keep in sync with legend. */
export const EDGE_STROKE_SHARED = `rgba(107, 158, 255, ${EDGE_STROKE_ALPHA})`;
export const EDGE_STROKE_FRESH = `rgba(94, 224, 212, ${EDGE_STROKE_ALPHA})`;

/** Legend copy + swatches (single source for UI). */
export const GRAPH_LEGEND = {
  nodes: [
    { key: "provider" as const, label: "Provider", palette: PROVIDER },
    { key: "fresh_instance" as const, label: "Fresh instance", palette: FRESH_INSTANCE },
    { key: "collection" as const, label: "Collection", palette: COLLECTION },
    { key: "value" as const, label: "Value", palette: VALUE },
    { key: "alias" as const, label: "Alias", palette: ALIAS },
    { key: "external" as const, label: "External", palette: EXTERNAL_AS_VALUE },
  ],
  edges: [
    { key: "shared" as const, label: "Shared dependency", stroke: EDGE_STROKE_SHARED, dashed: false },
    { key: "fresh" as const, label: "Fresh call", stroke: EDGE_STROKE_FRESH, dashed: true },
  ],
} as const;
