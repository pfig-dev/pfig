import ELK from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "reactflow";

import { BINDING_NODE_WIDTH } from "./graphBindingConstants";
import type { GraphNodeVM } from "./types";
import { graphInputPorts } from "./graphPlanDiff";

const elk = new ELK();

/** West-side target handles for ELK (same order as BindingNode). */
export function collectKwargPortIds(n: GraphNodeVM): string[] {
  return [...graphInputPorts(n)].map((port) => `kw-${port}`);
}

export function layoutNodeSize(n: Node): { width: number; height: number } {
  const d = n.data as { estimatedWidth?: number; estimatedHeight?: number };
  return {
    width: d?.estimatedWidth ?? BINDING_NODE_WIDTH,
    height: d?.estimatedHeight ?? 120,
  };
}

/** Build ELK graph with per-kwarg target ports on provider nodes and `out` on all nodes. */
export async function layoutWithElk(
  nodes: Node[],
  edges: Edge[],
  options: { separateConnectedComponents?: boolean } = {},
): Promise<Node[]> {
  if (nodes.length === 0) return nodes;

  const children = nodes.map((n) => {
    const { width, height } = layoutNodeSize(n);
    const pdata = n.data as GraphNodeVM & { kwargPortIds?: string[] };
    const ports = [{ id: "out", layoutOptions: { "elk.port.side": "EAST", "elk.port.index": "0" } }];
    const kwPorts = pdata?.kwargPortIds ?? [];
    const kwCount = kwPorts.length;
    // ELK FIXED_ORDER: explicit port indices. Clockwise from top-left, WEST is bottom→top:
    // index 1 = bottom kw row, kwCount = top row (matches BindingNode).
    for (let i = 0; i < kwCount; i++) {
      const pid = kwPorts[i];
      const westIndex = kwCount - i;
      ports.push({
        id: pid,
        layoutOptions: {
          "elk.port.side": "WEST",
          "elk.port.index": String(westIndex),
        },
      });
    }
    return {
      id: n.id,
      width,
      height,
      layoutOptions: {
        "elk.portConstraints": "FIXED_ORDER",
      },
      ports,
    };
  });

  const elkEdges = edges.map((e) => {
    return {
      id: e.id,
      sources: [e.source],
      targets: [e.target],
      sourcePort: e.sourceHandle ?? "out",
      targetPort: e.targetHandle ?? "kw-in",
    };
  });

  const graph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      ...(options.separateConnectedComponents === false ? { "elk.separateConnectedComponents": "false" } : {}),
      // More room between nodes and layers so edge bundles from RF have space to spread.
      "elk.spacing.nodeNode": "28",
      "elk.layered.spacing.nodeNodeBetweenLayers": "120",
      "elk.layered.spacing.edgeNodeBetweenLayers": "36",
      "elk.spacing.edgeEdge": "28",
      "elk.padding": "[24,24,24,24]",
      // Improve placement / crossing reduction for dense provider graphs.
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      // 0 = always run greedy-switch post-process (helps uncross edges on larger graphs).
      "elk.layered.crossingMinimization.greedySwitch.activationThreshold": "0",
      "elk.layered.layering.strategy": "NETWORK_SIMPLEX",
      // Default layered: west-side input ports are ordered by incoming edges, not JSON port order,
      // which fights our BindingNode kw row order (signature order from the LSP). Prefer model order.
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.layered.considerModelOrder.portModelOrder": "true",
    },
    children,
    edges: elkEdges,
  };

  const layouted = await elk.layout(graph);

  const posById = new Map<string, { x: number; y: number }>();
  for (const ch of layouted.children ?? []) {
    if (ch.id && ch.x != null && ch.y != null) {
      posById.set(ch.id, { x: ch.x, y: ch.y });
    }
  }

  return nodes.map((n) => {
    const p = posById.get(n.id);
    if (!p) return n;
    return {
      ...n,
      position: { x: p.x, y: p.y },
    };
  });
}
