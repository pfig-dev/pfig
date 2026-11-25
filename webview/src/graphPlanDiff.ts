import { FRESH_BASE_PORT, type FieldDelta, type GraphComparison, type GraphNodeVM, type GraphViewModel } from "./types";

export function diffFieldPort(field: FieldDelta): string | undefined {
  const [kind, key] = field.path[0];
  if (kind === "kwarg") return String(key);
  if (kind === "arg") return `#${Number(key) + 1}`;
  return undefined;
}

/** Historical argument rows, in the same order as their rendered sections. */
export function omittedArgumentRows(node: GraphNodeVM) {
  const removed: { port: string; field: FieldDelta }[] = [];
  const notAccepted: typeof removed = [];
  for (const field of node.diff?.fields ?? []) {
    const port = diffFieldPort(field);
    if (port !== undefined && field.before.present && !field.after.present) {
      (field.notAccepted ? notAccepted : removed).push({ port, field });
    }
  }
  return { removed, notAccepted };
}

/** Argument rows that remain visible when a provider or fresh instance is collapsed. */
export function changedArgumentPorts(node: GraphNodeVM): Set<string> {
  return new Set((node.diff?.fields ?? []).flatMap((field) => {
    const port = diffFieldPort(field);
    return port === undefined ? [] : [port];
  }));
}

/** Walk backwards through the old graph, stopping at surviving named bindings. */
export function removedBranch(diff: GraphComparison, root: string): Set<string> {
  const byId = new Map(diff.baseOnlyNodes.map((n) => [n.id, n]));
  const aliases = new Map(diff.removals.flatMap((r) => r.aliases.map((id) => [id, r.aliases] as const)));
  const incoming = new Map<string, string[]>();
  for (const { target, source } of diff.baseOnlyEdges) {
    const deps = incoming.get(target) ?? [];
    deps.push(source);
    incoming.set(target, deps);
  }
  const branch = new Set<string>();
  const visited = new Set<string>();
  const pending = [root];
  while (pending.length) {
    const id = pending.pop()!;
    if (visited.has(id)) continue;
    visited.add(id);
    if (!byId.has(id)) continue;
    branch.add(id);
    pending.push(...(aliases.get(id) ?? []), ...(incoming.get(id) ?? []));
  }
  return branch;
}

export function graphInputPorts(node: GraphNodeVM): Set<string> {
  const extra = node.oldPorts?.map((slot) => slot.port) ?? [];
  const { removed, notAccepted } = omittedArgumentRows(node);
  const omitted = [...removed, ...notAccepted].map(({ port }) => port);
  const rows = node.provider?.kwargs ?? node.freshInstance?.overlayKwargs;
  if (rows) {
    return new Set([...(node.freshInstance ? [FRESH_BASE_PORT] : []), ...rows.map((k) => k.name), ...omitted, ...extra]);
  }
  if (node.collection?.slots.length) return new Set([...node.collection.slots.map((s) => s.port), ...omitted, ...extra]);
  return new Set(["__binding__", ...omitted, ...extra]);
}

/** Reveal synthetic links to surviving consumers, not downstream removed branches. */
export function locateRemovedBranch(diff: GraphComparison, root: string): Set<string> {
  const branch = removedBranch(diff, root);
  const removed = new Set(diff.baseOnlyNodes.map((n) => n.id));
  const bridges = new Set(diff.baseOnlyNodes.filter((n) =>
    n.flags?.isAnonymous && !removed.has(n.diff?.ownerId ?? "")
  ).map((n) => n.id));
  const outgoing = new Map<string, string[]>();
  for (const { source, target } of diff.baseOnlyEdges) {
    if (!bridges.has(target)) continue;
    const consumers = outgoing.get(source) ?? [];
    consumers.push(target);
    outgoing.set(source, consumers);
  }
  for (const id of branch) {
    for (const target of outgoing.get(id) ?? []) branch.add(target);
  }
  return branch;
}

export function withRemovalOverlay(vm: GraphViewModel, active: ReadonlySet<string>, showAllOldWires: boolean): GraphViewModel {
  const diff = vm.diff;
  if (!diff || (!active.size && !showAllOldWires)) return vm;
  const baseById = new Map(diff.baseOnlyNodes.map((n) => [n.id, n]));
  const contextIds = new Set<string>();
  for (const edge of diff.baseOnlyEdges) {
    if (showAllOldWires || active.has(edge.source) || active.has(edge.target)) {
      if ((baseById.has(edge.source) && !active.has(edge.source)) ||
          (baseById.has(edge.target) && !active.has(edge.target))) continue;
      contextIds.add(edge.source);
      contextIds.add(edge.target);
    }
  }
  const nodes = [
    ...vm.nodes,
    ...diff.contextNodes.filter((n) => contextIds.has(n.id)),
    ...diff.baseOnlyNodes.filter((n) => active.has(n.id)),
  ];
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const oldPorts = new Map<string, Map<string, string>>();
  const portsById = new Map(nodes.map((n) => [n.id, graphInputPorts(n)]));
  const edges = diff.baseOnlyEdges.filter((e) => {
    if (!nodesById.has(e.source) || !nodesById.has(e.target)) return false;
    return showAllOldWires || active.has(e.source) || active.has(e.target);
  });
  for (const { target, targetPort, basePortLabel } of edges) {
    if (portsById.get(target)!.has(targetPort)) continue;
    const ports = oldPorts.get(target) ?? new Map();
    ports.set(targetPort, basePortLabel ?? targetPort);
    oldPorts.set(target, ports);
  }
  return {
    ...vm,
    nodes: nodes.map((n) => oldPorts.has(n.id) ? {
      ...n, oldPorts: [...oldPorts.get(n.id)!].map(([port, label]) => ({ port, label })),
    } : n),
    edges: [...vm.edges, ...edges],
  };
}
