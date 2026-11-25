import type { GraphNodeVM, GraphViewModel, KwargVM } from "./types";

function definitionKey(n: GraphNodeVM): string | null {
  if (n.diff?.logicalId) return `removed:${n.diff.logicalId}`;
  if (n.syntheticOrigin) return JSON.stringify(n.syntheticOrigin);
  if (!n.activeDefinition) {
    return null;
  }
  return JSON.stringify({
    uri: n.activeDefinition.uri,
    range: n.activeDefinition.range,
  });
}

function mergeFingerprint(n: GraphNodeVM): string {
  return JSON.stringify({
    kind: n.kind,
    fn: n.provider?.callableFqn ?? null,
    value: n.binding?.origin ?? n.binding?.valuePreview ?? null,
    ext: n.binding?.isExternal ?? null,
    ...(n.diff ? { diff: n.diff } : {}),
    ...(n.oldPorts ? { oldPorts: n.oldPorts } : {}),
  });
}

function hash32(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(31, h) + s.charCodeAt(i);
    h |= 0;
  }
  return (h >>> 0).toString(16);
}

function canonicalIdForCluster(defKey: string, fp: string): string {
  return `__pfig_src_${hash32(`${defKey}::${fp}`)}`;
}

function patchKwargRefs(k: KwargVM, pathToCanonical: Map<string, string>): KwargVM {
  const nextOverlays = k.overlayKwargs?.map((ok) => patchKwargRefs(ok, pathToCanonical));
  const rid = k.refTargetId;
  if (rid && pathToCanonical.has(rid)) {
    const canon = pathToCanonical.get(rid)!;
    return {
      ...k,
      refQualPath: k.refQualPath ?? rid,
      refTargetId: canon,
      overlayKwargs: nextOverlays !== undefined ? nextOverlays : k.overlayKwargs,
    };
  }
  return {
    ...k,
    overlayKwargs: nextOverlays ?? k.overlayKwargs,
  };
}

function patchNodeRefs(n: GraphNodeVM, pathToCanonical: Map<string, string>): GraphNodeVM {
  let out = n;
  if (n.provider) {
    out = {
      ...out,
      provider: {
        ...n.provider,
        kwargs: n.provider.kwargs.map((k) => patchKwargRefs(k, pathToCanonical)),
      },
    };
  }
  if (n.freshInstance) {
    out = {
      ...out,
      freshInstance: {
        ...n.freshInstance,
        ...(n.diff?.status === "removed" ? { baseProviderId: pathToCanonical.get(n.freshInstance.baseProviderId) ?? n.freshInstance.baseProviderId } : {}),
        overlayKwargs: n.freshInstance.overlayKwargs.map((k) =>
          patchKwargRefs(k, pathToCanonical)
        ),
      },
    };
  }
  return out;
}

function primaryPathFromSorted(paths: string[]): string {
  return paths.reduce((a, b) => (a.length <= b.length ? a : b));
}

function leafName(path: string): string {
  const i = path.lastIndexOf("/");
  return i >= 0 ? path.slice(i + 1) : path;
}

function mergeClusterNodes(nodes: GraphNodeVM[], sortedPaths: string[], canonical: string): GraphNodeVM {
  const primary = primaryPathFromSorted(sortedPaths);
  const rep = nodes.find((x) => x.id === primary) ?? nodes[0]!;
  const anyReachable = nodes.some((x) => x.flags?.isReachable !== false);
  const anyEntry = nodes.some((x) => x.flags?.isEntrypoint);
  const mergedFlags = {
    ...rep.flags,
    isReachable: anyReachable,
    isEntrypoint: anyEntry,
  };
  return {
    ...rep,
    id: canonical,
    bindingName: rep.flags?.isAnonymous ? rep.bindingName : leafName(primary),
    namespace: null,
    pathAliases: sortedPaths,
    primaryPathId: primary,
    flags: mergedFlags,
  };
}

/**
 * Merge nodes that share the same `activeDefinition` (source binding) into one card per semantic
 * fingerprint; remap edges and kwarg refs to canonical ids. When disabled, returns the input vm.
 * The graph webview always enables this for display; `enabled: false` is for tests and no-op use.
 */
export function transformCompactNamespaces(vm: GraphViewModel, enabled: boolean): GraphViewModel {
  if (!enabled || !vm.ok) {
    return vm;
  }

  const byCluster = new Map<string, GraphNodeVM[]>();
  for (const n of vm.nodes) {
    const dk = definitionKey(n);
    if (dk === null) {
      byCluster.set(`__singleton__:${n.id}`, [n]);
      continue;
    }
    const fp = mergeFingerprint(n);
    const ck = `${dk}::${fp}`;
    const g = byCluster.get(ck);
    if (g) {
      g.push(n);
    } else {
      byCluster.set(ck, [n]);
    }
  }

  const pathToCanonical = new Map<string, string>();
  const newNodes: GraphNodeVM[] = [];

  for (const [, group] of byCluster) {
    if (group.length === 1) {
      const n = group[0]!;
      pathToCanonical.set(n.id, n.id);
      newNodes.push(n);
      continue;
    }

    const sortedPaths = [...new Set(group.map((x) => x.id))].sort((a, b) =>
      a.localeCompare(b)
    );
    const dk = definitionKey(group[0]!);
    const fp = mergeFingerprint(group[0]!);
    const canonical =
      dk === null ? sortedPaths[0]! : canonicalIdForCluster(dk, fp);
    for (const p of sortedPaths) {
      pathToCanonical.set(p, canonical);
    }
    newNodes.push(mergeClusterNodes(group, sortedPaths, canonical));
  }

  const patchedNodes = newNodes.map((n) => patchNodeRefs(n, pathToCanonical));

  // Keep every reference and its original endpoints for display-time bundling.
  const edges = vm.edges.map((edge) => ({
    ...edge,
    source: pathToCanonical.get(edge.source) ?? edge.source,
    target: pathToCanonical.get(edge.target) ?? edge.target,
    sourcePath: edge.sourcePath ?? edge.source,
    targetPath: edge.targetPath ?? edge.target,
  }));

  return { ...vm, nodes: patchedNodes, edges };
}
