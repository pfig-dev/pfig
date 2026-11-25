import { describe, expect, it } from "vitest";

import { transformCompactNamespaces } from "./graphCompactNamespaces";
import type { GraphEdgeVM, GraphNodeVM, GraphViewModel, KwargVM, RangeJson } from "./types";

const range: RangeJson = {
  start: { line: 2, character: 0 },
  end: { line: 2, character: 8 },
};
const sharedDef = { uri: "file:///consts.pfig", range };

function providerNode(
  id: string,
  kwargs: KwargVM[],
  extra: Partial<GraphNodeVM> = {}
): GraphNodeVM {
  return {
    id,
    bindingName: id.split("/").pop() ?? id,
    namespace: id.includes("/") ? id.slice(0, id.lastIndexOf("/")) : null,
    kind: "provider",
    provider: {
      callableName: "P",
      callableFqn: "m.P",
      kwargs,
    },
    freshInstance: null,
    binding: null,
    activeDefinition: sharedDef,
    ...extra,
  };
}

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

describe("transformCompactNamespaces", () => {
  it("returns the same reference when disabled", () => {
    const vm = vmBase({
      nodes: [
        providerNode("a/x", [], { id: "a/x", activeDefinition: sharedDef }),
        providerNode("b/x", [], { id: "b/x", activeDefinition: sharedDef }),
      ],
      edges: [],
    });
    expect(transformCompactNamespaces(vm, false)).toBe(vm);
  });

  it("merges two paths with the same activeDefinition and remaps kwargs + edges", () => {
    const leaf = "pretrained";
    const p1 = providerNode(`c/${leaf}`, [], {
      id: `c/${leaf}`,
      activeDefinition: sharedDef,
      flags: { isReachable: true },
    });
    const p2 = providerNode(`pol/c/${leaf}`, [], {
      id: `pol/c/${leaf}`,
      activeDefinition: sharedDef,
      flags: { isReachable: false },
    });
    const use1: KwargVM = {
      name: "m",
      valueKind: "ref",
      refTargetId: `c/${leaf}`,
      inlinePreview: null,
      overlayKwargs: null,
    };
    const use2: KwargVM = {
      name: "n",
      valueKind: "ref",
      refTargetId: `pol/c/${leaf}`,
      inlinePreview: null,
      overlayKwargs: null,
    };
    const consumer = providerNode("train", [use1, use2], {
      id: "train",
      activeDefinition: { uri: "file:///train.pfig", range: { ...range, start: { line: 0, character: 0 } } },
      flags: { isReachable: true },
    });
    const e1: GraphEdgeVM = {
      id: "e0",
      source: `c/${leaf}`,
      target: "train",
      targetPort: "m",
      refKind: "shared",
    };
    const e2: GraphEdgeVM = {
      id: "e1",
      source: `pol/c/${leaf}`,
      target: "train",
      targetPort: "n",
      refKind: "shared",
    };
    const vm = vmBase({
      nodes: [p1, p2, consumer],
      edges: [e1, e2],
    });

    const out = transformCompactNamespaces(vm, true);
    expect(out.nodes).toHaveLength(2);
    const merged = out.nodes.find((n) => n.pathAliases?.length === 2);
    expect(merged).toBeDefined();
    expect(merged!.pathAliases).toEqual([`c/${leaf}`, `pol/c/${leaf}`]);
    expect(merged!.flags?.isReachable).toBe(true);

    const canon = merged!.id;
    expect(out.edges.every((e) => e.source === canon || e.target === canon)).toBe(true);
    const mKw = out.nodes.find((n) => n.id === "train")!.provider!.kwargs.find((k) => k.name === "m")!;
    const nKw = out.nodes.find((n) => n.id === "train")!.provider!.kwargs.find((k) => k.name === "n")!;
    expect(mKw.refTargetId).toBe(canon);
    expect(mKw.refQualPath).toBe(`c/${leaf}`);
    expect(nKw.refTargetId).toBe(canon);
    expect(nKw.refQualPath).toBe(`pol/c/${leaf}`);

    const byPort = new Map(out.edges.map((e) => [e.targetPort, e]));
    expect(byPort.get("m")!.sourcePath).toBe(`c/${leaf}`);
    expect(byPort.get("n")!.sourcePath).toBe(`pol/c/${leaf}`);
  });

  it("merges inline alias copies but keeps separate call sites and their labels", () => {
    const nodes = ["left", "right", "left", "right"].map((slot, i) =>
      providerNode(`__inline/${i}`, [], {
        activeDefinition: null, flags: { isAnonymous: true }, bindingName: "<inline>",
        syntheticOrigin: ["base.pfig", "consumer", slot],
      })
    );
    const out = transformCompactNamespaces(vmBase({ nodes }), true);
    expect(out.nodes.map(n => n.pathAliases)).toEqual([
      ["__inline/0", "__inline/2"], ["__inline/1", "__inline/3"],
    ]);
    expect(out.nodes.map(n => n.bindingName)).toEqual(["<inline>", "<inline>"]);
  });

  it("does not merge when mergeFingerprint differs", () => {
    const p1 = providerNode("a/x", [], {
      id: "a/x",
      activeDefinition: sharedDef,
      provider: { callableName: "A", callableFqn: "m.A", kwargs: [] },
    });
    const p2 = providerNode("b/x", [], {
      id: "b/x",
      activeDefinition: sharedDef,
      provider: { callableName: "B", callableFqn: "m.B", kwargs: [] },
    });
    const out = transformCompactNamespaces(vmBase({ nodes: [p1, p2], edges: [] }), true);
    expect(out.nodes).toHaveLength(2);
    expect(out.nodes.every((n) => !n.pathAliases?.length)).toBe(true);
  });
});
