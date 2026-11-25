import { describe, expect, it } from "vitest";
import { bindingFlowEdges } from "./graphBindingFlowEdges";
import { transformCompactNamespaces } from "./graphCompactNamespaces";
import { bundleBindingEdges } from "./graphEdgeBundles";
import { transformFoldValueNodes } from "./graphFoldValueNodes";
import { buildGraphOverview, graphSourceUris, overviewGroupId } from "./graphOverview";
import { changedArgumentPorts, graphInputPorts, locateRemovedBranch, omittedArgumentRows, removedBranch, withRemovalOverlay } from "./graphPlanDiff";
import type { FieldDelta, GraphComparison, GraphEdgeVM, GraphNodeVM, GraphViewModel } from "./types";

const counts = { added: 1, modified: 1, removed: 3 };
const sourceFileUri = "file:///transforms.pfig";
const node = (id: string, status?: "removed" | "added" | "modified"): GraphNodeVM => ({
  id, bindingName: id, kind: "value", activeDefinition: null, provider: null, freshInstance: null,
  binding: { valuePreview: "1" }, sourceFileUri, ...(status ? { diff: { status, fields: [] } } : {}),
});
const edge = (source: string, target: string, port: string, diffStatus: GraphEdgeVM["diffStatus"], refKind = "shared"): GraphEdgeVM => ({
  id: [source, target, port, diffStatus].join(":"), source, target, refKind, targetPort: port, diffStatus,
});
const cmp: GraphComparison = {
  baseLabel: "base.pfig", proposedLabel: "proposed.pfig", counts, nodesById: {},
  byFile: { [`file:${sourceFileUri}`]: counts },
  removals: ["leaf", "parent", "another"].map((id) => ({ id, aliases: [id], fileUri: sourceFileUri })),
  baseOnlyNodes: ["leaf", "parent", "another"].map((id) => node(id, "removed")),
  baseOnlyEdges: [
    edge("leaf", "parent", "__binding__", "removed"),
    edge("leaf", "another", "__binding__", "removed"),
    { ...edge("parent", "consumer", "oldArgument", "removed", "fresh"), basePortLabel: "oldArgument" },
  ],
  contextNodes: [],
};
const consumer: GraphNodeVM = {
  ...node("consumer", "modified"), kind: "provider", binding: null,
  provider: { callableFqn: "dict", callableName: "dict", kwargs: [{ name: "newArgument", valueKind: "ref" }] },
};
const vm: GraphViewModel = {
  ok: true, mode: "all", nodes: [node("new", "added"), consumer],
  edges: [edge("new", "consumer", "newArgument", "added")], diff: cmp,
};

describe("plan comparison", () => {
  it("follows incoming removed edges and hides a shared dependency without hiding other consumers", () => {
    expect(removedBranch(cmp, "leaf")).toEqual(new Set(["leaf"]));
    expect(removedBranch(cmp, "parent")).toEqual(new Set(["parent", "leaf"]));
    expect(locateRemovedBranch(cmp, "parent")).toEqual(new Set(["parent", "leaf"]));
    const active = new Set(["parent", "leaf", "another"]);
    for (const id of removedBranch(cmp, "parent")) active.delete(id);
    const overlay = withRemovalOverlay(vm, active, true);
    expect(overlay.nodes.map((n) => n.id)).toEqual(["new", "consumer", "another"]);
    expect(overlay.edges).toEqual(vm.edges);
  });

  it("keeps old target ports and removed values visible in Compact mode", () => {
    const overlay = withRemovalOverlay(vm, removedBranch(cmp, "parent"), false);
    const compact = transformFoldValueNodes(overlay, true);
    expect(compact.nodes.map((n) => n.id)).toEqual(["new", "consumer", "leaf", "parent"]);
    expect(compact.nodes.find((n) => n.id === "consumer")?.oldPorts).toEqual([
      { port: "oldArgument", label: "oldArgument" },
    ]);
    expect(compact.edges.filter((e) => e.diffStatus === "removed")).toHaveLength(2);
  });

  it("keeps file totals independent of the overlay and skips old wires at collapsed files", () => {
    const overlay = withRemovalOverlay(vm, new Set(["parent", "leaf"]), false);
    const build = (current: GraphViewModel, expanded: boolean) =>
      buildGraphOverview(current, new Set(expanded ? [overviewGroupId(consumer)] : []), graphSourceUris(current));
    expect(build(vm, false).groups[0].diffCounts).toEqual(counts);
    expect(build(overlay, false).groups[0].diffCounts).toEqual(counts);
    expect(build(overlay, false).links.some((link) => link.bundle?.edge.diffStatus === "removed")).toBe(false);
    expect(build(overlay, true).links.filter((link) => link.bundle?.edge.diffStatus === "removed")).toHaveLength(2);
  });

  it("keeps added, old, and common routes distinct even with collapsed args", () => {
    const bundles = bundleBindingEdges([
      edge("new", "consumer", "oldArgument", "removed", "fresh"),
      edge("new", "consumer", "newArgument", "added", "fresh"),
      edge("new", "consumer", "another", undefined),
    ]);
    const routes = bindingFlowEdges(vm.nodes, bundles, (n) => n === consumer);
    expect(routes.map((e) => e.style?.strokeDasharray)).toEqual(["6 4", "2 6", undefined]);
    expect(routes.map((e) => e.targetHandle)).toEqual(Array(3).fill("kw-__collapsed__"));
    expect(routes[0].data?.[0].summary).toBe("Removed · Fresh call");
  });

  it("groups omitted arguments by signature compatibility and keeps their ports", () => {
    const before = { present: true, preview: "@old", title: "@old" } as const;
    const after = { present: false } as const;
    const rejected: FieldDelta = { path: [["kwarg", "rejected"]], before, after, notAccepted: true };
    const removed: FieldDelta = { path: [["kwarg", "removed"]], before, after };
    const modified: GraphNodeVM = {
      ...consumer, diff: { status: "modified", fields: [rejected, removed] },
    };
    expect(omittedArgumentRows(modified)).toEqual({
      removed: [{ port: "removed", field: removed }],
      notAccepted: [{ port: "rejected", field: rejected }],
    });
    expect([...graphInputPorts(modified)]).toEqual(["newArgument", "removed", "rejected"]);
  });

  it("keeps only changed argument ports separate on a collapsed diff card", () => {
    const ref = { present: true, preview: "@new", title: "@new" } as const;
    const fields: FieldDelta[] = [
      { path: [["kwarg", "newArgument"]], before: { present: false }, after: ref },
      { path: [["kwarg", "oldArgument"]], before: ref, after: { present: false } },
      { path: [["callable", ""]], before: ref, after: ref },
    ];
    const modified: GraphNodeVM = {
      ...consumer, diff: { status: "modified", fields },
      provider: { ...consumer.provider!, kwargs: [
        ...consumer.provider!.kwargs, { name: "unchanged", valueKind: "ref" },
      ] },
    };
    expect(changedArgumentPorts(modified)).toEqual(new Set(["newArgument", "oldArgument"]));

    const edges = [
      edge("new", "consumer", "newArgument", "added"),
      edge("parent", "consumer", "oldArgument", "removed"),
      edge("new", "consumer", "unchanged", undefined),
    ];
    const hooks = (collapsed: boolean) =>
      bindingFlowEdges([modified], bundleBindingEdges(edges), () => collapsed)
        .map((wire) => wire.targetHandle);
    expect(hooks(true)).toEqual(["kw-newArgument", "kw-oldArgument", "kw-__collapsed__"]);
    expect(hooks(false)).toEqual(["kw-newArgument", "kw-oldArgument", "kw-unchanged"]);

    const source = { ...node("new", "added"), sourceFileUri: "file:///other.pfig" };
    const graph = { ...vm, nodes: [source, modified], edges: [edges[0], edges[2]] };
    const overview = buildGraphOverview(graph, new Set([overviewGroupId(modified)]), graphSourceUris(graph));
    expect(overview.links[0].bundle?.edge.targetPort).toBe("newArgument");
    expect(overview.links[0].source).toBe(overviewGroupId(source));
    expect(overview.links[1].bundle).toBeUndefined();
  });

  it("compacts removed aliases but keeps conflicting proposed statuses separate", () => {
    const location = { uri: sourceFileUri, range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } } };
    const removedAlias = (id: string): GraphNodeVM => ({
      ...node(id), diff: { status: "removed", fields: [], logicalId: "old", ownerId: "old" },
    });
    const compact = transformCompactNamespaces({
      ...vm, edges: [], nodes: [
        { ...node("a", "added"), activeDefinition: location },
        { ...node("b"), activeDefinition: location },
        removedAlias("alias/old"), removedAlias("old"),
      ],
    }, true);
    expect(compact.nodes).toHaveLength(3);
    expect(compact.nodes.find((n) => n.diff?.status === "removed")?.pathAliases).toEqual(["alias/old", "old"]);
  });
});
