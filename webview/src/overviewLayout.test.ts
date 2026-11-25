import type { Edge, Node } from "reactflow";
import { describe, expect, it } from "vitest";

import { layoutNodeSize } from "./elkLayout";
import { layoutBindingsOnly, layoutOverview, OVERVIEW_FILE_LAYOUT, OVERVIEW_HEADER_HEIGHT, selectedOverviewFile } from "./overviewLayout";

function node(id: string, width: number, height: number, parentNode?: string): Node {
  return {
    id, parentNode, position: { x: 0, y: 0 },
    data: { estimatedWidth: width, estimatedHeight: height, kwargPortIds: ["kw-__binding__"] },
  };
}

describe("selectedOverviewFile", () => {
  const data = node("file:data", 260, 76);
  const read = node("read", 280, 90, data.id);
  const write = node("write", 280, 90, data.id);
  const train = node("file:train", 260, 76);
  const run = node("run", 280, 90, train.id);
  const select = (ids: string[]) => [data, train, read, write, run].map((n) => ({
    ...n, selected: ids.includes(n.id),
  }));

  it("activates the full file from either its header or any of its children", () => {
    const active = { id: data.id, members: new Set([data.id, read.id, write.id]) };
    expect(selectedOverviewFile(select([data.id]))).toEqual(active);
    expect(selectedOverviewFile(select([read.id, write.id]))).toEqual(active);
  });

  it("does not raise collapsed files or ambiguous multi-file selections", () => {
    expect(selectedOverviewFile(select([]))).toBeNull();
    expect(selectedOverviewFile([{ ...train, selected: true }])).toBeNull();
    expect(selectedOverviewFile(select([read.id, run.id]))).toBeNull();
  });
});

describe("layoutOverview", () => {
  it("keeps unrelated bindings in the same columns when their heights change", async () => {
    const run = (first: number, second: number) => layoutOverview([
      node("file:readers", 260, 76),
      node("lake", 280, first, "file:readers"),
      node("nvidia", 280, second, "file:readers"),
    ], []);
    const [, lake, nvidia] = await run(64, 700);
    const [, tallerLake, shorterNvidia] = await run(700, 64);
    expect(lake.position.x).toBeLessThan(nvidia.position.x);
    expect(tallerLake.position).toEqual(lake.position);
    expect(shorterNvidia.position).toEqual(nvidia.position);
  });
  it("keeps connected sibling order when one card expands", async () => {
    const ids = ["lake", "ous", "us"];
    const links = ids.map((id) => ({ id, source: "source", target: id }));
    const layout = async (lakeHeight: number) => {
      const laid = await layoutOverview([
        node("file:assets", 260, 76),
        node("source", 280, 64, "file:assets"),
        ...ids.map((id) => node(id, 280, id === "lake" ? lakeHeight : 64, "file:assets")),
      ], links);
      const column = laid.filter(({ id }) => ids.includes(id)).sort((a, b) => a.position.y - b.position.y);
      expect(new Set(column.map(({ position }) => position.x)).size).toBe(1);
      for (let i = 1; i < column.length; i++) {
        const prev = column[i - 1];
        expect(column[i].position.y).toBeGreaterThanOrEqual(prev.position.y + layoutNodeSize(prev).height);
      }
      return column.map(({ id }) => id);
    };
    expect(await layout(240)).toEqual(await layout(64));
  });

  it("contains bindings and lays out files using the expanded bounds", async () => {
    const nodes = [
      node("file:data", 260, 76), node("file:train", 260, 76),
      node("read", 280, 90, "file:data"), node("filter", 280, 150, "file:data"),
    ];
    const edges: Edge[] = [
      { id: "inner", source: "read", target: "filter" },
      { id: "outer", source: "filter", target: "file:train" },
    ];
    const [data, train, read, filter] = await layoutOverview(nodes, edges);
    expect([data.id, train.id, read.id, filter.id]).toEqual(nodes.map(({ id }) => id));
    expect(read.position.x).toBeLessThan(filter.position.x);
    const bounds = layoutNodeSize(data);
    for (const child of [read, filter]) {
      const size = layoutNodeSize(child);
      expect(child.parentNode).toBe(data.id);
      expect(child.position.x).toBeGreaterThan(0);
      expect(child.position.y).toBeGreaterThan(OVERVIEW_HEADER_HEIGHT);
      expect(child.position.x + size.width).toBeLessThan(bounds.width);
      expect(child.position.y + size.height).toBeLessThan(bounds.height);
    }
    expect(train.position.x).toBeGreaterThan(data.position.x + bounds.width);
  });
});

describe("layoutBindingsOnly", () => {
  it("uses expanded Overview positions without file nodes or parent constraints", async () => {
    const files = ["file:data", "file:train", "group:unlocated"].map((id) => ({
      ...node(id, 0, 0), data: OVERVIEW_FILE_LAYOUT,
    }));
    const bindings = [
      node("source", 280, 90), node("reader", 280, 340), node("train", 280, 100),
      node("other", 280, 80), node("external", 280, 80),
    ];
    const parents = new Map([
      ["source", files[0].id], ["reader", files[0].id], ["train", files[1].id],
      ["other", files[1].id], ["external", files[2].id],
    ]);
    const edges = [
      { id: "internal", source: "source", target: "reader" },
      { id: "cross-file", source: "reader", target: "train" },
      { id: "external-input", source: "external", target: "train" },
    ];
    const grouped = bindings.map((child) => ({
      ...child, parentNode: parents.get(child.id), extent: "parent" as const,
    }));
    const placed = await layoutOverview([...files, ...grouped], edges);
    const flat = await layoutBindingsOnly(bindings, edges, parents);
    expect(flat.map(({ id }) => id)).toEqual(bindings.map(({ id }) => id));
    for (const binding of flat) {
      const { position, parentNode } = placed.find(({ id }) => id === binding.id)!;
      const { position: file } = placed.find(({ id }) => id === parentNode)!;
      expect(binding.position).toEqual({ x: file.x + position.x, y: file.y + position.y });
      expect(binding.parentNode).toBeUndefined();
      expect(binding.extent).toBeUndefined();
    }
  });
});
