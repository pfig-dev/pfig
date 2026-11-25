import { describe, expect, it } from "vitest";

import { focusGraph, searchBindings } from "./graphFocus";
import { transformFoldValueNodes } from "./graphFoldValueNodes";
import type { GraphEdgeVM, GraphNodeVM, GraphViewModel } from "./types";

function node(id: string, extra: Partial<GraphNodeVM> = {}): GraphNodeVM {
  return {
    id, bindingName: id, kind: "provider", provider: null, binding: null,
    freshInstance: null, activeDefinition: null, ...extra,
  };
}

function edge(source: string, target: string, targetPort = source): GraphEdgeVM {
  return { id: `${source}-${target}:${targetPort}`, source, target, targetPort, refKind: "shared" };
}

const vm: GraphViewModel = {
  ok: true,
  mode: "reachable",
  nodes: ["data", "model", "trainer", "report", "sibling", "unrelated"].map((id) => node(id)),
  edges: [
    edge("data", "model"), edge("model", "trainer", "main"),
    edge("model", "trainer", "extra"), edge("trainer", "report"), edge("model", "sibling"),
  ],
};

describe("searchBindings", () => {
  it("ranks exact paths before prefixes and substrings, ignoring anonymous nodes", () => {
    const nodes = [
      node("a/trainer_config"), node("not_a_trainer"), node("z/trainer", { bindingName: "trainer" }),
      node("trainer", { flags: { isAnonymous: true } }),
    ];
    expect(searchBindings(nodes, " @TraINer ", 2).map(({ id }) => id)).toEqual([
      "z/trainer", "a/trainer_config",
    ]);
    expect(searchBindings(nodes, "", 8)).toEqual([]);
  });

  it("shows the matched alias or primary path while navigating by canonical id", () => {
    const n = node("__pfig_src_abc", {
      bindingName: "trainer", primaryPathId: "root/trainer", pathAliases: ["root/trainer", "training/trainer"],
    });
    expect(searchBindings([n], "training/trainer", 8)[0]).toMatchObject({ id: n.id, label: "training/trainer" });
    expect(searchBindings([n], "trainer", 8)[0]).toMatchObject({ id: n.id, label: "root/trainer" });
    expect(searchBindings([n], "__pfig_src", 8)).toEqual([]);
  });
});

describe("focusGraph", () => {
  it("walks dependencies transitively and keeps parallel argument edges", () => {
    const result = focusGraph(vm, "trainer", "dependencies");
    expect(result.nodes.map(({ id }) => id)).toEqual(["data", "model", "trainer"]);
    expect(result.edges).toEqual(vm.edges.slice(0, 3));
    expect(focusGraph(vm, "trainer", "dependents").nodes.map(({ id }) => id)).toEqual(["trainer", "report"]);
  });

  it("does not follow sibling consumers in both directions", () => {
    const result = focusGraph(vm, "trainer", "both");
    expect(result.nodes.map(({ id }) => id)).toEqual(["data", "model", "trainer", "report"]);
    expect(result.edges).toEqual(vm.edges.slice(0, 4));
    expect(focusGraph(vm, "missing", "both")).toBe(vm);
  });

  it("preserves focus on a literal hidden in Compact mode", () => {
    const value = node("data", { kind: "value", binding: { valuePreview: "42" } });
    const withValue = { ...vm, nodes: [value, ...vm.nodes.slice(1)] };
    expect(transformFoldValueNodes(withValue, true).nodes).not.toContain(value);
    const folded = transformFoldValueNodes(withValue, true, "data");
    const focused = focusGraph(folded, "data", "dependencies");
    expect(focused.nodes).toEqual([value]);
    expect(focused.edges).toEqual([]);
  });
});
