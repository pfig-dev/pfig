import { FRESH_BASE_PORT, type GraphEdgeVM, type GraphNodeVM } from "./types";

export interface BindingEdgeBundle {
  edge: GraphEdgeVM;
  count: number;
}

export interface BindingEdgeDetails {
  wiring: string;
  summary: string;
}

/** Bundle references of one kind to the same input, counting each consumer alias only once. */
export function bundleBindingEdges(edges: readonly GraphEdgeVM[]): BindingEdgeBundle[] {
  const bundles = new Map<string, BindingEdgeBundle & { byConsumer: Map<string, number> }>();
  for (const edge of edges) {
    const key = JSON.stringify([edge.source, edge.target, edge.targetPort, edge.refKind, edge.diffStatus]);
    let bundle = bundles.get(key);
    if (!bundle) {
      bundle = { edge, count: 0, byConsumer: new Map() };
      bundles.set(key, bundle);
    }
    const consumer = edge.targetPath ?? edge.target;
    const calls = (bundle.byConsumer.get(consumer) ?? 0) + 1;
    bundle.byConsumer.set(consumer, calls);
    // Alias copies are not extra calls. Multiple references in one consumer still count.
    bundle.count = Math.max(bundle.count, calls);
  }
  return [...bundles.values()].map(({ edge, count }) => ({ edge, count }));
}

export function describeBindingEdge(
  { edge, count }: BindingEdgeBundle, nodes: ReadonlyMap<string, GraphNodeVM>,
): BindingEdgeDetails {
  const source = nodes.get(edge.source)?.bindingName ?? edge.source;
  const target = nodes.get(edge.target);
  const port = edge.targetPort;
  const slot = target?.collection?.slots.find((s) => s.port === port);
  const input = slot ? `[${slot.label}]` : port === "__binding__" || port === FRESH_BASE_PORT ? "" : `.${edge.basePortLabel ?? port}`;
  const fresh = edge.refKind === "fresh";
  const kind = fresh
    ? count > 1 ? `${count} fresh calls` : "Fresh call"
    : count > 1 ? `${count} shared references` : "Shared reference";
  return {
    wiring: `${target?.bindingName ?? edge.target}${input} ← @${source}${fresh ? "()" : ""}`,
    summary: edge.diffStatus ? `${edge.diffStatus === "removed" ? "Removed" : "Added"} · ${kind}` : kind,
  };
}
