import { describe, expect, it } from "vitest";
import { findNavigationNode } from "./graphNavigation";
import { buildGraphOverview, graphSourceUris, overviewGroupId } from "./graphOverview";
import { transformFoldValueNodes } from "./graphFoldValueNodes";
import type { GraphNodeVM, GraphViewModel } from "./types";

function node(id: string, extra: Partial<GraphNodeVM> = {}): GraphNodeVM {
  return { id, bindingName: id, kind: "value", provider: null, binding: { valuePreview: "42" }, freshInstance: null, activeDefinition: null, ...extra };
}

describe("graph navigation", () => {
  it("resolves references and compact namespace aliases to the target card", () => {
    const target = node("consts/lr", { pathAliases: ["consts/lr", "trainer/consts/lr"] });
    expect(findNavigationNode([target], "consts/lr")).toBe(target);
    expect(findNavigationNode([target], "trainer/consts/lr")).toBe(target);
    expect(findNavigationNode([target], "missing")).toBeUndefined();
  });

  it("reveals only the referenced folded value in its own file", () => {
    const value = node("lr", { sourceFileUri: "file:///config/values.pfig" });
    const otherValue = node("seed", { sourceFileUri: value.sourceFileUri });
    const trainer = node("trainer", {
      kind: "provider", binding: null, sourceFileUri: "file:///config/train.pfig",
      provider: { callableName: "Trainer", callableFqn: "train.Trainer", kwargs: [{ name: "lr", valueKind: "ref", refTargetId: "lr" }] },
    });
    const vm: GraphViewModel = {
      ok: true, nodes: [value, otherValue, trainer],
      edges: [{ id: "e", source: "lr", target: "trainer", targetPort: "lr", refKind: "shared" }],
      mode: "all",
    };
    const folded = transformFoldValueNodes(vm, true);
    const kwarg = folded.nodes.find((n) => n.id === "trainer")!.provider!.kwargs[0]!;
    expect(kwarg.valueKind).toBe("foldedRef");
    expect(kwarg.foldedTargetId).toBe("lr");
    const target = findNavigationNode(vm.nodes, kwarg.foldedTargetId!)!;
    expect(target).toBe(value);

    const uris = graphSourceUris(vm);
    const expanded = new Set([overviewGroupId(trainer)]);
    expect(buildGraphOverview(folded, expanded, uris).groups.map(({ id }) => id)).toEqual([...expanded]);

    expanded.add(overviewGroupId(target));
    const revealed = transformFoldValueNodes(vm, true, target.id);
    const overview = buildGraphOverview(revealed, expanded, uris);
    expect(overview.bindings.map(({ id }) => id)).toEqual(["lr", "trainer"]);
    expect(overview.bindingParents.get(target.id)).toBe(overviewGroupId(value));
    expect(overview.links).toEqual([{
      source: "lr", target: "trainer", count: 1, bundle: { edge: revealed.edges[0], count: 1 },
    }]);
  });
});
