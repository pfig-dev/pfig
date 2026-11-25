import { describe, expect, it } from "vitest";

import { transformCompactNamespaces } from "./graphCompactNamespaces";
import { bundleBindingEdges, describeBindingEdge } from "./graphEdgeBundles";
import type { GraphEdgeVM, GraphNodeVM, GraphViewModel } from "./types";

function node(id: string, line: number): GraphNodeVM {
  return {
    id, bindingName: id.split("/").at(-1)!, kind: "value",
    provider: null, freshInstance: null, binding: null,
    activeDefinition: {
      uri: "file:///base.pfig",
      range: { start: { line, character: 0 }, end: { line, character: 1 } },
    },
  };
}

describe("binding edge bundles", () => {
  it("counts authored fresh calls and a shared input without multiplying by consumer aliases", () => {
    const nodes: GraphNodeVM[] = [];
    const edges: GraphEdgeVM[] = [];
    for (let i = 0; i < 11; i++) {
      const source = `module${i}/view_entry`;
      const target = `module${i}/view_entries`;
      const views = `module${i}/views`;
      nodes.push(node(views, 3), node(source, 1), {
        ...node(target, 2),
        binding: { origin: ["file:///base.pfig", "view_entries"], valuePreview: `[@${source}(...)]` },
      });
      edges.push({ id: `${i}:entries`, source: target, target: views, targetPort: "entries", refKind: "shared" });
      for (let j = 0; j < 3; j++) {
        edges.push({ id: `${i}:${j}`, source, target, targetPort: "__binding__", refKind: "fresh" });
      }
    }
    const vm: GraphViewModel = { ok: true, mode: "reachable", nodes, edges };
    const compact = transformCompactNamespaces(vm, true);
    const bundles = bundleBindingEdges(compact.edges);
    const nodesById = new Map(compact.nodes.map((n) => [n.id, n]));
    expect(compact.nodes).toHaveLength(3);
    expect(compact.edges).toHaveLength(44);
    expect(bundles.map((bundle) => describeBindingEdge(bundle, nodesById))).toEqual([
      { wiring: "views.entries ← @view_entries", summary: "Shared reference" },
      { wiring: "view_entries ← @view_entry()", summary: "3 fresh calls" },
    ]);
  });

  it("keeps different input ports, reference kinds, and consumers separate", () => {
    const first: GraphEdgeVM = { id: "e0", source: "config", target: "runner", targetPort: "train", refKind: "shared" };
    const bundles = bundleBindingEdges([
      first,
      { ...first, id: "e1", targetPort: "validation" },
      { ...first, id: "e2", refKind: "fresh" },
      { ...first, id: "e3", target: "other" },
    ]);
    expect(bundles.map(({ edge }) => edge.id)).toEqual(["e0", "e1", "e2", "e3"]);
    expect(bundles.every((bundle) => bundle.count === 1)).toBe(true);
  });

  it("counts distinct uses of source aliases in one consumer", () => {
    const first: GraphEdgeVM = {
      id: "e0", source: "template", sourcePath: "a/template",
      target: "tasks", targetPath: "tasks", targetPort: "__binding__", refKind: "fresh",
    };
    const bundles = bundleBindingEdges([first, { ...first, id: "e1", sourcePath: "b/template" }]);
    expect(bundles).toEqual([{ edge: first, count: 2 }]);
  });
});
