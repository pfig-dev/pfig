import type { Node, Viewport, XYPosition } from "reactflow";

type PositionedNode = Pick<Node, "id" | "position" | "parentNode">;

function graphPosition(nodes: readonly PositionedNode[], id: string): XYPosition | undefined {
  const node = nodes.find((n) => n.id === id);
  if (!node || !node.parentNode) return node?.position;
  const parent = graphPosition(nodes, node.parentNode);
  return parent && { x: parent.x + node.position.x, y: parent.y + node.position.y };
}

/** Keep the anchor at the same screen point after ELK moves it. */
export function anchorViewport(
  viewport: Viewport,
  before: readonly PositionedNode[],
  after: readonly PositionedNode[],
  id: string,
): Viewport {
  const from = graphPosition(before, id);
  const to = graphPosition(after, id);
  if (!from || !to || (from.x === to.x && from.y === to.y)) return viewport;
  return {
    x: viewport.x + (from.x - to.x) * viewport.zoom,
    y: viewport.y + (from.y - to.y) * viewport.zoom,
    zoom: viewport.zoom,
  };
}
