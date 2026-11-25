import { MarkerType, type Edge } from "reactflow";

import { COLLAPSED_PROVIDER_INPUT } from "./graphBindingConstants";
import { describeBindingEdge, type BindingEdgeBundle, type BindingEdgeDetails } from "./graphEdgeBundles";
import { EDGE_STROKE_FRESH, EDGE_STROKE_SHARED } from "./graphNodeTheme";
import { FRESH_BASE_PORT, type GraphNodeVM } from "./types";
import { changedArgumentPorts } from "./graphPlanDiff";

export const EDGE_COUNT_STYLE = {
  labelStyle: { fill: "var(--pfig-fg)", fontSize: 11 },
  labelBgStyle: { fill: "var(--pfig-bar)" },
  labelBgPadding: [4, 3] as [number, number],
};

export function bindingFlowEdges(
  nodes: readonly GraphNodeVM[], bundles: readonly BindingEdgeBundle[],
  isCollapsed: (node: GraphNodeVM) => boolean, visibleSources?: ReadonlyMap<string, string>,
): Edge<BindingEdgeDetails[]>[] {
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const changedPortsById = new Map(nodes.filter((n) => n.diff?.fields.length)
    .map((n) => [n.id, changedArgumentPorts(n)]));
  const routes = new Map<string, { source: string; targetHandle: string; inputs: BindingEdgeBundle[] }>();
  for (const bundle of bundles) {
    const { edge } = bundle;
    const source = visibleSources?.get(edge.id) ?? edge.source;
    const target = nodeById.get(edge.target);
    const basePort = edge.targetPort === FRESH_BASE_PORT && (edge.diffStatus !== "removed" || target?.freshInstance);
    const changedPort = changedPortsById.get(edge.target)?.has(edge.targetPort);
    const hidden = target && isCollapsed(target) && !basePort && !changedPort;
    const targetHandle = hidden ? COLLAPSED_PROVIDER_INPUT : `kw-${edge.targetPort}`;
    // Hidden arguments share a physical handle. Keep every logical input for inspection.
    const key = hidden ? JSON.stringify([source, edge.target, edge.diffStatus]) : edge.id;
    const route = routes.get(key);
    if (route) route.inputs.push(bundle);
    else routes.set(key, { source, targetHandle, inputs: [bundle] });
  }

  return [...routes.values()].map(({ source, targetHandle, inputs }) => {
    const { edge } = inputs[0];
    const count = inputs.reduce((total, input) => total + input.count, 0);
    const isFresh = inputs.every((input) => input.edge.refKind === "fresh");
    const stroke = edge.diffStatus ? `var(--pfig-diff-${edge.diffStatus})` : isFresh ? EDGE_STROKE_FRESH : EDGE_STROKE_SHARED;
    const details = inputs.map((input) => describeBindingEdge(input, nodeById));
    return {
      id: edge.id,
      source,
      target: edge.target,
      sourceHandle: "out",
      targetHandle,
      ariaLabel: details.map((d) => `${d.wiring}: ${d.summary}`).join("; "),
      data: details,
      ...(count > 1 ? { markerEnd: { type: MarkerType.ArrowClosed, color: stroke } } : {}),
      ...(count > 1 && (isFresh || inputs.length > 1) ? { label: String(count), ...EDGE_COUNT_STYLE } : {}),
      animated: false,
      interactionWidth: 22,
      style: {
        stroke,
        strokeWidth: 2,
        ...(edge.diffStatus === "removed" ? { strokeDasharray: "6 4" } :
          isFresh ? { strokeDasharray: "2 6", strokeLinecap: "round" as const } : {}),
      },
    };
  });
}
