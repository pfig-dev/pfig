import { describe, expect, it } from "vitest";

import { buildGraphOverview, graphSourceUris, soloGraphOverview } from "./graphOverview";
import { FRESH_BASE_PORT, type GraphNodeVM, type GraphViewModel } from "./types";

const root = "file:///config/";
const main = `${root}ensemble/train.pfig`;
const readers = `${root}common/data/readers.pfig`;
const model = `${root}common/model/providers.pfig`;
const entry = "file:" + main;
const data = "file:" + readers;
const models = "file:" + model;

function node(id: string, uri: string | null, flags?: GraphNodeVM["flags"]): GraphNodeVM {
  return {
    id, bindingName: id, kind: "provider", provider: null, binding: null,
    freshInstance: null, activeDefinition: null, sourceFileUri: uri, flags,
  };
}

const vm: GraphViewModel = {
  ok: true,
  mode: "reachable",
  entryFileUri: main,
  nodes: [
    node("read", readers, { isOverridden: true }), node("select", readers),
    node("model", model), node("train", main), node("external", null),
  ],
  edges: [
    { id: "0", source: "read", target: "train", targetPort: "data", refKind: "shared" },
    { id: "1", source: "select", target: "train", targetPort: "validation", refKind: "shared" },
    { id: "2", source: "model", target: "train", targetPort: "model", refKind: "shared" },
    { id: "3", source: "select", target: "read", targetPort: "selection", refKind: "fresh" },
    { id: "4", source: "train", target: "read", targetPort: "config", refKind: "fresh" },
  ],
};

function overview(...expanded: string[]) {
  return buildGraphOverview(vm, new Set(expanded), graphSourceUris(vm));
}

describe("buildGraphOverview", () => {
  it("shows files with their paths and counts directional links between them", () => {
    const { groups, bindings, links } = overview();
    expect(bindings).toEqual([]);
    expect(groups.map(({ id }) => id)).toEqual([data, models, entry, "group:unlocated"]);
    expect(groups[0]).toMatchObject({
      kind: "file", label: "common/data/readers.pfig", cardCount: 2, overrideCount: 1,
    });
    expect(groups[1]).toMatchObject({ kind: "file", label: "common/model/providers.pfig" });
    expect(groups.find((group) => group.id === entry)).toMatchObject({
      entryFile: true, kind: "file", label: "ensemble/train.pfig",
    });
    expect(links).toMatchObject([
      { source: data, target: entry, count: 2 },
      { source: models, target: entry, count: 1 },
      { source: entry, target: data, count: 1 },
    ]);
  });

  it("does not count copies of the same reference in collapsed file links", () => {
    const fresh = { ...vm.edges[0], refKind: "fresh" };
    const expandedAliases = ["a/train", "b/train"].flatMap((targetPath) =>
      ["first", "second"].map((call) => ({ ...fresh, id: `${targetPath}/${call}`, targetPath }))
    );
    const next = { ...vm, edges: [...expandedAliases, vm.edges[1]] };
    expect(buildGraphOverview(next, new Set(), graphSourceUris(next)).links)
      .toMatchObject([{ source: data, target: entry, count: 3, details: [
        { wiring: "train.data ← @read()", summary: "2 fresh calls" },
        { wiring: "train.validation ← @select", summary: "Shared reference" },
      ] }]);
  });

  it("expands files to bindings without hiding external links", () => {
    const { groups, bindings, bindingParents, links } = overview(data, entry);
    expect(bindings.map(({ id }) => id)).toEqual(["read", "select", "train"]);
    expect(groups.find(({ id }) => id === data)).toMatchObject({
      label: "common/data/readers.pfig", expanded: true, cardCount: 2, overrideCount: 1,
    });
    expect(bindingParents).toEqual(new Map([["read", data], ["select", data], ["train", entry]]));
    expect(links.find(({ source }) => source === "read")).toMatchObject({
      target: "train", bundle: { edge: { targetPort: "data", refKind: "shared" }, count: 1 },
    });
    expect(links.find(({ source }) => source === models)).toMatchObject({
      source: models, target: "train", count: 1,
    });
  });

  it("keeps the fresh base link separate when the provider's file is collapsed", () => {
    const baseEdge = { ...vm.edges[0], targetPort: FRESH_BASE_PORT, refKind: "fresh" };
    const next = { ...vm, edges: [baseEdge, ...vm.edges.slice(1)] };
    const { links } = buildGraphOverview(next, new Set([entry]), graphSourceUris(next));
    expect(links).toContainEqual({
      source: data, target: "train", count: 1, bundle: { edge: baseEdge, count: 1 },
    });
    expect(links).toContainEqual(expect.objectContaining({ source: data, target: "train", count: 1, details: [
      { wiring: "train.validation ← @select", summary: "Shared reference" },
    ] }));
  });

  it("collapses one file while leaving other open files alone", () => {
    expect(overview(data, models).openGroups.map(({ id }) => id)).toEqual([data, models]);
    const collapsed = overview(models);
    expect(collapsed.groups.find(({ id }) => id === data)?.expanded).toBe(false);
    expect(collapsed.groups.find(({ id }) => id === models)?.expanded).toBe(true);
    expect(collapsed.openGroups.map(({ id }) => id)).toEqual([models]);
    expect(collapsed.bindings.map(({ id }) => id)).toEqual(["model"]);
  });

  it("walks the file graph in the chosen direction without changing expansion state", () => {
    const full = overview(data, models);
    const dependents = soloGraphOverview(full, vm, models, "dependents");
    expect(dependents.groups.map(({ id }) => id)).toEqual([data, models, entry]);
    expect(dependents.openGroups.map(({ id }) => id)).toEqual([data, models]);
    expect(dependents.bindings.map(({ id }) => id)).toEqual(["read", "select", "model"]);
    expect(dependents.links).toContainEqual({
      source: "select", target: "read", count: 1, bundle: { edge: vm.edges[3], count: 1 },
    });

    const upstream = soloGraphOverview(full, vm, models, "dependencies");
    expect(upstream.groups.map(({ id }) => id)).toEqual([models]);
    expect(upstream.bindings.map(({ id }) => id)).toEqual(["model"]);

    // Walking downstream from data must not pull in a sibling dependency of train.
    expect(soloGraphOverview(full, vm, data, "dependents").groups.map(({ id }) => id)).toEqual([data, entry]);
    expect(soloGraphOverview(full, vm, data, "both").groups.map(({ id }) => id)).toEqual([data, models, entry]);
    expect(full.openGroups.map(({ id }) => id)).toEqual([data, models]);
    expect(soloGraphOverview(overview(), vm, data, "both").groups.map(({ id }) => id)).toEqual([data, models, entry]);
    expect(soloGraphOverview(full, vm, null, "both")).toBe(full);
    expect(soloGraphOverview(full, vm, "missing", "both")).toBe(full);
  });

  it("does not create a dependency path through unrelated bindings in the same file", () => {
    const next = {
      ...vm,
      nodes: [...vm.nodes, node("side", main)],
      edges: vm.edges.map((edge) => edge.id === "4" ? { ...edge, source: "side" } : edge),
    };
    const full = buildGraphOverview(next, new Set(), graphSourceUris(next));
    expect(soloGraphOverview(full, next, data, "dependencies").groups.map(({ id }) => id)).toEqual([data, entry]);
  });

  it("keeps folder accents through expansion, solo, and filtering bindings", () => {
    const cache = `${root}common/data/cache.pfig`;
    const next = { ...vm, nodes: [...vm.nodes, node("cache", cache)] };
    const uris = graphSourceUris(next);
    const full = buildGraphOverview(next, new Set(), uris);
    const accent = full.groups.find(({ id }) => id === data)?.folderAccent;
    expect(accent).toBeDefined();
    expect(full.groups.find(({ id }) => id === `file:${cache}`)?.folderAccent).toEqual(accent);
    const filtered = { ...next, nodes: next.nodes.filter(({ id }) => id !== "cache") };
    const expanded = buildGraphOverview(filtered, new Set([data]), uris);
    expect(soloGraphOverview(expanded, filtered, data, "both").groups.find(({ id }) => id === data)?.folderAccent).toEqual(accent);
    expect(full.groups.find(({ kind }) => kind === "unlocated")?.folderAccent).toBeUndefined();
  });

  it("uses the original file before an override site", () => {
    const overridden = {
      ...vm.nodes[0],
      activeDefinition: { uri: main, range: { start: { line: 0, character: 0 }, end: { line: 0, character: 5 } } },
    };
    const next = { ...vm, nodes: [overridden, ...vm.nodes.slice(1)] };
    const groups = buildGraphOverview(next, new Set(), graphSourceUris(next)).groups;
    expect(groups.find(({ id }) => id === data)?.overrideCount).toBe(1);
    expect(groups.find(({ id }) => id === entry)?.overrideCount).toBe(0);
  });
});
