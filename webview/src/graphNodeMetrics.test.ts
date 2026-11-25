import { describe, expect, it } from "vitest";

import { COLLAPSED_PROVIDER_HEIGHT } from "./graphBindingConstants";
import { canCollapseKwargs, estimateBindingCardSize } from "./graphNodeMetrics";
import type { GraphNodeVM } from "./types";

describe("collapsed provider arguments", () => {
  it("uses one height regardless of alias count, definition, or kwargs", () => {
    const node: GraphNodeVM = {
      id: "provider",
      bindingName: "provider",
      kind: "provider",
      provider: {
        callableName: "Provider",
        callableFqn: "module.Provider",
        kwargs: ["reader", "writer", "geometry", "options"].map((name) => ({ name, valueKind: "ref" })),
      },
      binding: null,
      freshInstance: null,
      activeDefinition: null,
    };
    const open = estimateBindingCardSize(node);
    const closed = estimateBindingCardSize(node, true);
    expect(closed.width).toBe(open.width);
    expect(closed.height).toBeLessThan(open.height);
    expect(closed.height).toBe(COLLAPSED_PROVIDER_HEIGHT);
    expect(estimateBindingCardSize({
      ...node,
      pathAliases: ["alias/a", "alias/b", "alias/c"],
      provider: { ...node.provider!, kwargs: [] },
    }, true)).toEqual(closed);
  });

  it("collapses fresh instances but not externals", () => {
    const external: GraphNodeVM = {
      id: "root",
      bindingName: "root",
      kind: "external",
      provider: null,
      binding: { isExternal: true },
      freshInstance: null,
      activeDefinition: null,
    };
    const fresh: GraphNodeVM = {
      ...external,
      id: "lake",
      bindingName: "lake",
      kind: "fresh_instance",
      binding: null,
      freshInstance: {
        baseProviderId: "common/readers/lake_reader",
        baseProviderBindingName: "lake_reader",
        overlayKwargs: [{ name: "split", valueKind: "literal", inlinePreview: "val" }],
      },
    };
    expect(canCollapseKwargs(fresh)).toBe(true);
    expect(estimateBindingCardSize(fresh, true).height).toBe(COLLAPSED_PROVIDER_HEIGHT);
    expect(estimateBindingCardSize(fresh).height).toBeGreaterThan(COLLAPSED_PROVIDER_HEIGHT);
    expect(canCollapseKwargs(external)).toBe(false);
    expect(estimateBindingCardSize(external, true)).toEqual(estimateBindingCardSize(external));
  });
});
