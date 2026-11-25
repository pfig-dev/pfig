import type { GraphNodeVM, GraphViewModel, KwargVM } from "./types";

/** Visible text cap so ELK row heights stay stable; full string in `title`. */
const FOLDED_KW_DISPLAY_MAX = 44;

function truncateFoldLabel(full: string, maxLen = FOLDED_KW_DISPLAY_MAX): string {
  if (full.length <= maxLen) return full;
  return `${full.slice(0, Math.max(0, maxLen - 1))}…`;
}

function sourceReachableByNodeId(nodes: GraphNodeVM[]): Map<string, boolean> {
  const m = new Map<string, boolean>();
  for (const node of nodes) {
    m.set(node.id, node.flags?.isReachable !== false);
  }
  return m;
}

function isFoldableValueNode(
  n: GraphNodeVM,
  edges: GraphViewModel["edges"],
  sourceReachable: Map<string, boolean>
): boolean {
  if (n.kind !== "value") return false;
  if (n.provider || n.collection || n.freshInstance) return false;
  const hasIncomingBindingFromReachable = edges.some(
    (e) =>
      e.target === n.id &&
      e.targetPort === "__binding__" &&
      sourceReachable.get(e.source) !== false
  );
  if (hasIncomingBindingFromReachable) return false;
  const hasOutgoing = edges.some((e) => e.source === n.id);
  return hasOutgoing;
}

/** Named value bindings with no consumers (common in mode=all); nothing references them as kwargs. */
function isOrphanValueNode(n: GraphNodeVM, edges: GraphViewModel["edges"]): boolean {
  if (n.kind !== "value") return false;
  if (n.provider || n.collection || n.freshInstance) return false;
  return !edges.some((e) => e.source === n.id);
}

function patchKwarg(
  k: KwargVM,
  foldIds: Set<string>,
  foldPreviewById: Map<string, string>
): KwargVM {
  const rid = k.refTargetId;
  const nextOverlays = k.overlayKwargs?.map((ok) => patchKwarg(ok, foldIds, foldPreviewById));

  if (rid && foldIds.has(rid)) {
    const preview = foldPreviewById.get(rid) ?? "";
    const displayPath = k.refQualPath ?? rid;
    const full = `@${displayPath} = ${preview}`;
    return {
      ...k,
      valueKind: "foldedRef",
      refTargetId: null,
      foldedTargetId: rid,
      inlinePreview: null,
      overlayKwargs: nextOverlays !== undefined ? nextOverlays : k.overlayKwargs,
      foldedDisplayFull: full,
      foldedDisplayShort: truncateFoldLabel(full),
    };
  }

  return {
    ...k,
    overlayKwargs: nextOverlays ?? k.overlayKwargs,
  };
}

function patchNodeKwargs(
  n: GraphNodeVM,
  foldIds: Set<string>,
  foldPreviewById: Map<string, string>
): GraphNodeVM {
  let out = n;
  if (n.provider) {
    out = {
      ...out,
      provider: {
        ...n.provider,
        kwargs: n.provider.kwargs.map((k) => patchKwarg(k, foldIds, foldPreviewById)),
      },
    };
  }
  if (n.freshInstance) {
    out = {
      ...out,
      freshInstance: {
        ...n.freshInstance,
        overlayKwargs: n.freshInstance.overlayKwargs.map((k) =>
          patchKwarg(k, foldIds, foldPreviewById)
        ),
      },
    };
  }
  return out;
}

/**
 * Remove foldable value bindings (kwarg-only leaves plus mode=all orphans with no outgoing edges)
 * and show `@id = preview` on kwargs where refs pointed at removed nodes.
 */
export function transformFoldValueNodes(
  vm: GraphViewModel, enabled: boolean, keepId: string | null = null
): GraphViewModel {
  if (!enabled || !vm.ok) {
    return vm;
  }

  const sourceReachable = sourceReachableByNodeId(vm.nodes);
  // Keep old wire endpoints drawable; normal folding does not have a row for old references.
  const comparisonEndpoints = new Set(vm.edges.filter((e) => e.diffStatus).flatMap((e) => [e.source, e.target]));
  const foldIds = new Set(
    vm.nodes
      .filter((n) => isFoldableValueNode(n, vm.edges, sourceReachable))
      .map((n) => n.id)
  );
  for (const n of vm.nodes) {
    if (isOrphanValueNode(n, vm.edges)) {
      foldIds.add(n.id);
    }
  }

  for (const n of vm.nodes) {
    if (n.diff || comparisonEndpoints.has(n.id)) foldIds.delete(n.id);
  }
  if (keepId !== null) foldIds.delete(keepId);

  if (foldIds.size === 0) {
    return vm;
  }

  const foldPreviewById = new Map<string, string>();
  for (const n of vm.nodes) {
    if (foldIds.has(n.id)) {
      foldPreviewById.set(n.id, n.binding?.valuePreview ?? "");
    }
  }

  const newEdges = vm.edges
    .filter((e) => !foldIds.has(e.source) && !foldIds.has(e.target))
    .map((e, i) => vm.diff ? e : { ...e, id: `e${i}` });

  const newNodes = vm.nodes
    .filter((n) => !foldIds.has(n.id))
    .map((n) => patchNodeKwargs(n, foldIds, foldPreviewById));

  return {
    ...vm,
    nodes: newNodes,
    edges: newEdges,
  };
}
