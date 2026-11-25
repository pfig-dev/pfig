import type { GraphNodeVM, GraphViewModel } from "./types";

export type FocusDirection = "dependencies" | "dependents" | "both";

/** Search authored names and paths; keep canonical ids for navigation only. */
export function searchBindings(nodes: readonly GraphNodeVM[], query: string, limit: number) {
  const term = query.trim().toLowerCase().replace(/^@/, "");
  if (!term) return [];

  function matchRank(label: string): number {
    const s = label.toLowerCase();
    if (s === term) return 0;
    if (s.startsWith(term) || s.split("/").pop()?.startsWith(term)) return 1;
    return s.includes(term) ? 2 : Infinity;
  }

  const matches: { id: string; label: string; rank: number }[] = [];
  for (const node of nodes) {
    if (node.flags?.isAnonymous) continue;
    let label = node.primaryPathId ?? node.id;
    let rank = matchRank(label);
    for (const alias of node.pathAliases ?? []) {
      const aliasRank = matchRank(alias);
      if (aliasRank < rank) {
        label = alias;
        rank = aliasRank;
      }
    }
    rank = Math.min(rank, matchRank(node.bindingName));
    if (Number.isFinite(rank)) matches.push({ id: node.id, label, rank });
  }
  return matches.sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label)).slice(0, limit);
}

/** Walk dependencies and dependents separately; never turn around at a neighbor. */
export function focusNodeIds(
  edges: readonly { source: string; target: string }[], anchors: readonly string[], direction: FocusDirection,
): Set<string> {
  const visible = new Set(anchors);
  function walk(reverse: boolean) {
    const neighbors = new Map<string, string[]>();
    for (const { source, target } of edges) {
      const from = reverse ? target : source;
      const to = reverse ? source : target;
      const list = neighbors.get(from);
      if (list) list.push(to);
      else neighbors.set(from, [to]);
    }
    const queue = [...anchors];
    const visited = new Set(queue);
    for (let i = 0; i < queue.length; i++) {
      for (const id of neighbors.get(queue[i]) ?? []) {
        if (visited.has(id)) continue;
        visited.add(id);
        visible.add(id);
        queue.push(id);
      }
    }
  }

  // Walk the two directions separately so "both" does not include sibling consumers.
  if (direction !== "dependents") walk(true);
  if (direction !== "dependencies") walk(false);
  return visible;
}

export function focusGraph(vm: GraphViewModel, anchorId: string, direction: FocusDirection): GraphViewModel {
  if (!vm.ok || !vm.nodes.some((n) => n.id === anchorId)) return vm;

  const visible = focusNodeIds(vm.edges, [anchorId], direction);
  return {
    ...vm,
    nodes: vm.nodes.filter(({ id }) => visible.has(id)),
    edges: vm.edges.filter(({ source, target }) => visible.has(source) && visible.has(target)),
  };
}
