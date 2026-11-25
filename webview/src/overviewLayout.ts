import type { Edge, Node } from "reactflow";

import { layoutWithElk, layoutNodeSize } from "./elkLayout";
import { COLLAPSED_PROVIDER_HEIGHT } from "./graphBindingConstants";

export const OVERVIEW_CARD_WIDTH = 260;
export const OVERVIEW_HEADER_HEIGHT = 76;
export const OVERVIEW_FILE_LAYOUT = {
  estimatedWidth: OVERVIEW_CARD_WIDTH,
  estimatedHeight: OVERVIEW_HEADER_HEIGHT,
  kwargPortIds: ["kw-__binding__"],
};
const GROUP_PADDING = 18;

export const SELECTED_FILE_Z = { panel: 10, binding: 13 };

export function selectedOverviewFile(nodes: Node[]): { id: string; members: Set<string> } | null {
  const selected = nodes.filter(({ selected }) => selected);
  const owner = (node: Node) => node.parentNode ?? node.id;
  if (!selected.length) return null;

  const id = owner(selected[0]);
  if (selected.some((node) => owner(node) !== id)) return null;
  if (!nodes.some((node) => node.parentNode === id)) return null;

  return { id, members: new Set(nodes.filter((node) => owner(node) === id).map((node) => node.id)) };
}

/** Keep independent cards in file order, even when their heights change. */
function layoutIndependentBindings(nodes: Node[]): Node[] {
  const columns = Math.ceil(Math.sqrt(nodes.length));
  const gap = 24;
  const columnWidth = Math.max(...nodes.map((node) => layoutNodeSize(node).width));
  let y = 0;
  const positioned: Node[] = [];
  for (let i = 0; i < nodes.length; i += columns) {
    const row = nodes.slice(i, i + columns);
    positioned.push(...row.map((node, col) => ({
      ...node, position: { x: col * (columnWidth + gap), y },
    })));
    y += Math.max(...row.map((node) => layoutNodeSize(node).height)) + gap;
  }
  return positioned;
}

/** Use topology for card order, then reserve space for each measured height. */
async function layoutConnectedBindings(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const base = nodes.map((node) => ({
    ...node,
    data: { estimatedWidth: layoutNodeSize(node).width, estimatedHeight: COLLAPSED_PROVIDER_HEIGHT },
  }));
  const links = edges.map(({ id, source, target }) => ({ id, source, target }));
  const ordered = await layoutWithElk(base, links, { separateConnectedComponents: false });
  const originals = new Map(nodes.map((node) => [node.id, node]));
  const bottomByColumn = new Map<number, number>();
  const positions = new Map<string, Node["position"]>();
  for (const { id, position } of [...ordered].sort((a, b) => a.position.y - b.position.y)) {
    const { x } = position;
    const y = Math.max(position.y, bottomByColumn.get(x) ?? 0);
    positions.set(id, { x, y });
    bottomByColumn.set(x, y + layoutNodeSize(originals.get(id)!).height + 24);
  }
  return nodes.map((node) => ({ ...node, position: positions.get(node.id)! }));
}

/** Lay out each file's bindings, then place whole files without changing the rendered edges. */
export async function layoutOverview(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const roots: Node[] = [];
  const childrenByRoot = new Map<string, Node[]>();
  const rootByNode = new Map<string, string>();
  for (const node of nodes) {
    rootByNode.set(node.id, node.parentNode ?? node.id);
    if (!node.parentNode) {
      roots.push(node);
      continue;
    }
    const children = childrenByRoot.get(node.parentNode) ?? [];
    children.push(node);
    childrenByRoot.set(node.parentNode, children);
  }

  const internalEdges = new Map<string, Edge[]>();
  const fileEdges = new Map<string, Edge>();
  for (const edge of edges) {
    const source = rootByNode.get(edge.source);
    const target = rootByNode.get(edge.target);
    if (!source || !target) continue;
    if (source === target) {
      const local = internalEdges.get(source) ?? [];
      local.push(edge);
      internalEdges.set(source, local);
    } else {
      const id = JSON.stringify([source, target]);
      fileEdges.set(id, { id, source, target, sourceHandle: "out", targetHandle: "kw-__binding__" });
    }
  }

  const positionedChildren = new Map<string, Node[]>();
  const sizedRoots = await Promise.all(roots.map(async (root) => {
    const children = childrenByRoot.get(root.id);
    if (!children) return root;

    const links = internalEdges.get(root.id);
    const laid = links?.length
      ? await layoutConnectedBindings(children, links)
      : layoutIndependentBindings(children);
    const left = Math.min(...laid.map(({ position }) => position.x));
    const top = Math.min(...laid.map(({ position }) => position.y));
    let width = OVERVIEW_CARD_WIDTH;
    let height = OVERVIEW_HEADER_HEIGHT;
    const placed = laid.map((child) => {
      const pos = {
        x: child.position.x - left + GROUP_PADDING,
        y: child.position.y - top + OVERVIEW_HEADER_HEIGHT + GROUP_PADDING,
      };
      const size = layoutNodeSize(child);
      width = Math.max(width, pos.x + size.width + GROUP_PADDING);
      height = Math.max(height, pos.y + size.height + GROUP_PADDING);
      return { ...child, position: pos };
    });
    positionedChildren.set(root.id, placed);
    return { ...root, data: { ...root.data, estimatedWidth: width, estimatedHeight: height } };
  }));

  const positionedRoots = await layoutWithElk(sizedRoots, [...fileEdges.values()]);
  // React Flow requires parents to appear before their children.
  return [...positionedRoots, ...positionedRoots.flatMap(({ id }) => positionedChildren.get(id) ?? [])];
}

/** Place bindings as in expanded Overview, but return only absolute binding positions. */
export async function layoutBindingsOnly(
  bindings: Node[], edges: Edge[], fileParents: ReadonlyMap<string, string>,
): Promise<Node[]> {
  const files: Node[] = [...new Set(fileParents.values())].map((id) => ({
    id,
    position: { x: 0, y: 0 },
    data: OVERVIEW_FILE_LAYOUT,
  }));
  const grouped = bindings.map((node) => ({ ...node, parentNode: fileParents.get(node.id) }));
  const laid = await layoutOverview([...files, ...grouped], edges);
  const positions = new Map(laid.map(({ id, position }) => [id, position]));
  return bindings.map((node) => {
    const file = positions.get(fileParents.get(node.id)!)!;
    const child = positions.get(node.id)!;
    return { ...node, position: { x: file.x + child.x, y: file.y + child.y } };
  });
}
