import { describe, expect, it } from "vitest";

import { transformFoldValueNodes } from "./graphFoldValueNodes";
import type { GraphEdgeVM, GraphNodeVM, GraphViewModel, KwargVM } from "./types";

function vmBase(overrides: Partial<GraphViewModel> = {}): GraphViewModel {
  return {
    ok: true,
    error: null,
    mode: "all",
    nodes: [],
    edges: [],
    ...overrides,
  };
}

function providerNode(id: string, kwargs: KwargVM[]): GraphNodeVM {
  return {
    id,
    bindingName: id,
    kind: "provider",
    provider: {
      callableName: "C",
      callableFqn: "m.C",
      kwargs,
    },
    freshInstance: null,
    binding: null,
    activeDefinition: null,
  };
}

function valueNode(id: string, preview = "42"): GraphNodeVM {
  return {
    id,
    bindingName: id,
    kind: "value",
    provider: null,
    freshInstance: null,
    binding: { valuePreview: preview },
    activeDefinition: null,
  };
}

describe("transformFoldValueNodes", () => {
  it("removes orphan value nodes (no outgoing edges) when folding is enabled", () => {
    const orphan = valueNode("orphan_lit");
    const p = providerNode("p", []);
    const vm = vmBase({
      nodes: [p, orphan],
      edges: [],
    });

    const out = transformFoldValueNodes(vm, true);
    expect(out.nodes.map((n) => n.id)).toEqual(["p"]);
    expect(out.edges).toEqual([]);
  });

  it("still folds kwarg-only value leaves and patches refs", () => {
    const leaf = valueNode("leaf", "99");
    const p = providerNode("p", [
      {
        name: "x",
        valueKind: "ref",
        refTargetId: "leaf",
        inlinePreview: null,
        overlayKwargs: null,
      },
    ]);
    const e: GraphEdgeVM = {
      id: "e0",
      source: "leaf",
      target: "p",
      targetPort: "x",
      refKind: "shared",
    };
    const vm = vmBase({ nodes: [p, leaf], edges: [e] });

    const out = transformFoldValueNodes(vm, true);
    expect(out.nodes.map((n) => n.id)).toEqual(["p"]);
    const kw = out.nodes[0]!.provider!.kwargs[0]!;
    expect(kw.valueKind).toBe("foldedRef");
    expect(kw.foldedDisplayFull).toBe("@leaf = 99");
  });

  it("returns the same vm reference when folding is disabled", () => {
    const vm = vmBase({
      nodes: [providerNode("p", []), valueNode("o")],
      edges: [],
    });
    expect(transformFoldValueNodes(vm, false)).toBe(vm);
  });

  it("returns the same vm reference when fold applies to nothing", () => {
    const vm = vmBase({ ok: false, error: "x", nodes: [], edges: [] });
    expect(transformFoldValueNodes(vm, true)).toBe(vm);
  });
});
