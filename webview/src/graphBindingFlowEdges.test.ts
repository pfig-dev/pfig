import { describe, expect, it } from "vitest";

import { COLLAPSED_PROVIDER_INPUT } from "./graphBindingConstants";
import { bindingFlowEdges } from "./graphBindingFlowEdges";
import { bundleBindingEdges } from "./graphEdgeBundles";
import { FRESH_BASE_PORT, type GraphNodeVM } from "./types";

const run: GraphNodeVM = {
  id: "run", bindingName: "run", kind: "provider", provider: null, binding: null,
  freshInstance: null, activeDefinition: null,
};

describe("bindingFlowEdges", () => {
  it("combines hidden argument wires but keeps all details, counts, and the fresh base port", () => {
    const uses = ["first", "first", "second", FRESH_BASE_PORT].map((targetPort, i) => ({
      id: String(i), source: "model", target: "run", targetPort,
      refKind: targetPort === FRESH_BASE_PORT ? "fresh" : "shared",
    }));
    const bundles = bundleBindingEdges(uses);
    const collapsed = bindingFlowEdges([run], bundles, () => true);
    expect(collapsed.map((e) => e.targetHandle)).toEqual([COLLAPSED_PROVIDER_INPUT, `kw-${FRESH_BASE_PORT}`]);
    expect(collapsed[0].label).toBe("3");
    expect(collapsed[0].data).toEqual([
      { wiring: "run.first ← @model", summary: "2 shared references" },
      { wiring: "run.second ← @model", summary: "Shared reference" },
    ]);
    expect(bindingFlowEdges([run], bundles, () => false).map((e) => e.targetHandle))
      .toEqual(["kw-first", "kw-second", `kw-${FRESH_BASE_PORT}`]);
  });
});
