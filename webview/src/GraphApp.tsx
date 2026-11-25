import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type MouseEvent as ReactMouseEvent,
} from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  Panel,
  type Edge,
  type Node,
  type NodeTypes,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "reactflow";
import "reactflow/dist/style.css";

import { BindingNode } from "./BindingNode";
import { GraphFocusBar } from "./GraphFocusBar";
import { focusGraph, type FocusDirection } from "./graphFocus";
import {
  buildGraphOverview, graphSourceUris, overviewGroupId, soloGraphOverview, type GraphOverview,
} from "./graphOverview";
import { OverviewGroupNode, type OverviewCardData } from "./OverviewGroupNode";
import {
  layoutBindingsOnly, layoutOverview, OVERVIEW_FILE_LAYOUT, selectedOverviewFile, SELECTED_FILE_Z,
} from "./overviewLayout";
import { canCollapseKwargs, estimateBindingCardSize } from "./graphNodeMetrics";
import { COLLAPSED_PROVIDER_INPUT, OVERVIEW_GROUP_INPUT } from "./graphBindingConstants";
import { LegendExportNode } from "./LegendExportNode";
import {
  type EdgeHoverHighlight,
  computeGlowingNodeIds,
  edgeMatchesHighlight,
  GraphHighlightContext,
} from "./graphHighlightContext";
import { GraphLegend } from "./graphLegend";
import { EDGE_STROKE_SHARED } from "./graphNodeTheme";
import {
  measureBindingCardHeightsFromMeasureLayer,
  PFIG_MEASURE_ID_ATTR,
  waitNextPaint,
} from "./graphDomMeasure";
import { transformCompactNamespaces } from "./graphCompactNamespaces";
import { bundleBindingEdges, type BindingEdgeDetails } from "./graphEdgeBundles";
import { bindingFlowEdges, EDGE_COUNT_STYLE } from "./graphBindingFlowEdges";
import { EdgeReferenceDetails } from "./EdgeReferenceDetails";
import { transformFoldValueNodes } from "./graphFoldValueNodes";
import { collectKwargPortIds } from "./elkLayout";
import { FRESH_BASE_PORT, type GraphEdgeVM, type GraphNodeVM, type GraphViewModel } from "./types";
import { findNavigationNode } from "./graphNavigation";
import { anchorViewport } from "./graphViewport";
import { useNodeSingleClick } from "./useNodeSingleClick";
import "./graphExport.css";
import "./graphTheme.css";
import {
  PngExportController,
  type ExportPngOpts,
  type GraphExportColorMode,
} from "./graphPngExport";
import { createGraphHostTransport, defaultExportOptions } from "./hostTransport";
import { GraphDiffActions, GraphDiffControls } from "./GraphDiffControls";
import { changedArgumentPorts, locateRemovedBranch, omittedArgumentRows, removedBranch, withRemovalOverlay } from "./graphPlanDiff";

const nodeTypes: NodeTypes = { binding: BindingNode, sourceGroup: OverviewGroupNode, legend: LegendExportNode };
const OVERVIEW_THRESHOLD = 50;
const MAX_NODE_ZOOM = 8;
type GraphView = "overview" | "bindings";
type SoloTarget = { kind: "file" | "binding"; id: string; direction: FocusDirection };

/** Shared toolbar select styling. */
const TOOLBAR_SELECT_STYLE = {
  fontSize: 11,
  minWidth: 132,
  color: "var(--pfig-fg, #ccc)",
  background: "var(--pfig-bar, #1e1e1e)",
  border: "1px solid var(--pfig-border, #444)",
  borderRadius: 4,
  padding: "2px 4px",
} as const;

type LayoutProbe = {
  nodes: Node[];
  edges: Edge[];
  gen: number;
  hiddenFileParents: ReadonlyMap<string, string> | null;
};

type BindingCardData = ComponentProps<typeof BindingNode>["data"];

type KwargMode = "collapsed" | "expanded";

type KwargDisplay = {
  isCollapsed: (node: GraphNodeVM) => boolean;
  toggle: (id: string) => void;
};

function bindingFlowNodes(
  nodes: readonly GraphNodeVM[],
  anchorId: string | null,
  toggleSolo: (id: string, direction: FocusDirection) => void,
  kwargs: KwargDisplay,
  soloDirection: FocusDirection | null,
  groupedInputs?: ReadonlySet<string>
): Node[] {
  return nodes.map((n) => {
    const kwargsCollapsed = kwargs.isCollapsed(n);
    const { width: w, height: h } = estimateBindingCardSize(n, kwargsCollapsed);
    const kwargPortIds = collectKwargPortIds(n);
    const hasGroupedInput = !kwargsCollapsed && (groupedInputs?.has(n.id) ?? false);
    let collapsedPorts: string[] | null = null;
    if (kwargsCollapsed) {
      const changedPorts = changedArgumentPorts(n);
      const { removed, notAccepted } = omittedArgumentRows(n);
      const oldPorts = new Set([...removed, ...notAccepted].map(({ port }) => `kw-${port}`));
      collapsedPorts = [
        ...kwargPortIds.filter((id) => id === `kw-${FRESH_BASE_PORT}` || (changedPorts.has(id.slice(3)) && !oldPorts.has(id))),
        COLLAPSED_PROVIDER_INPUT,
        ...oldPorts,
      ];
    }
    return {
      id: n.id,
      type: "binding",
      zIndex: 2,
      position: { x: 0, y: 0 },
      ...(n.id === anchorId ? { className: "pfig-focus-card" } : {}),
      data: {
        ...n,
        freshInstance: n.freshInstance ?? null,
        kwargPortIds: collapsedPorts ?? (hasGroupedInput ? [...kwargPortIds, OVERVIEW_GROUP_INPUT] : kwargPortIds),
        hasGroupedInput,
        kwargsCollapsed,
        toggleKwargs: kwargs.toggle,
        soloDirection: n.id === anchorId ? soloDirection : null,
        toggleSolo,
        estimatedWidth: w,
        estimatedHeight: h,
      },
    };
  });
}

function overviewToFlow(
  allBindings: readonly GraphNodeVM[],
  { groups, bindings, bindingParents, links }: GraphOverview,
  kwargs: KwargDisplay,
  expand: (id: string) => void,
  collapse: (id: string) => void,
  fileSolo: SoloTarget | null,
  toggleFileSolo: (id: string, direction: FocusDirection, expanded: boolean) => void,
  toggleBindingSolo: (id: string, direction: FocusDirection) => void
): Pick<LayoutProbe, "nodes" | "edges"> {
  const groupIds = new Set(groups.map(({ id }) => id));
  const nodes: Node[] = [];
  for (const group of groups) {
    nodes.push({
      id: group.id,
      type: "sourceGroup",
      zIndex: group.expanded ? 1 : 2,
      className: group.expanded ? "pfig-expanded-file-frame" : undefined,
      position: { x: 0, y: 0 },
      draggable: false,
      selectable: group.expanded,
      data: {
        ...group,
        ...OVERVIEW_FILE_LAYOUT,
        expand,
        collapse,
        soloDirection: group.id === fileSolo?.id ? fileSolo.direction : null,
        toggleSolo: toggleFileSolo,
      },
    });
  }
  const groupedInputs = new Set(links.filter(({ bundle }) => !bundle).map(({ target }) => target));
  for (const node of bindingFlowNodes(bindings, null, toggleBindingSolo, kwargs, null, groupedInputs)) {
    nodes.push({ ...node, parentNode: bindingParents.get(node.id)!, extent: "parent" });
  }

  const exact = links.flatMap(({ bundle, source }) => bundle ? [{ bundle, source }] : []);
  const visibleSources = new Map(exact.map(({ bundle, source }) => [bundle.edge.id, source]));
  const edges = bindingFlowEdges(allBindings, exact.map(({ bundle }) => bundle), kwargs.isCollapsed, visibleSources);
  const collapsedIds = new Set(bindings.filter(kwargs.isCollapsed).map(({ id }) => id));
  for (const { source, target, count, bundle, details, diffStatus } of links) {
    if (bundle) continue;
    const stroke = diffStatus ? `var(--pfig-diff-${diffStatus})` : EDGE_STROKE_SHARED;
    edges.push({
      id: `bundle:${JSON.stringify(diffStatus ? [source, target, diffStatus] : [source, target])}`,
      source,
      target,
      sourceHandle: "out",
      targetHandle: collapsedIds.has(target)
        ? COLLAPSED_PROVIDER_INPUT
        : groupIds.has(target) ? "kw-__binding__" : OVERVIEW_GROUP_INPUT,
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
      label: String(count),
      ...EDGE_COUNT_STYLE,
      style: { stroke, strokeWidth: 1.5 },
      data: details,
      ariaLabel: details?.map(d => `${d.wiring}: ${d.summary}`).join("; "),
    });
  }
  return { nodes, edges };
}

const EDGE_GLOW_FILTER =
  "drop-shadow(0 0 4px rgba(140, 190, 255, 1)) drop-shadow(0 0 14px rgba(107, 158, 255, 0.7))";

function edgesWithHoverGlow(
  edges: Edge[],
  hl: EdgeHoverHighlight,
  hoveredEdgeId: string | null,
  activeFileMembers: ReadonlySet<string> | null,
  exporting: boolean
): Edge[] {
  return edges.map((e) => {
    const direct = !exporting && (e.selected || e.id === hoveredEdgeId);
    const connected = activeFileMembers?.has(e.source) || activeFileMembers?.has(e.target);
    if (activeFileMembers && !connected && !direct) {
      return { ...e, zIndex: 0, className: "pfig-background-edge" };
    }
    const glow = direct || edgeMatchesHighlight(e.source, e.target, e.targetHandle, hl);
    if (glow) {
      return {
        ...e,
        zIndex: direct ? 1500 : 0,
        style: {
          ...e.style,
          stroke: String(e.style?.stroke).startsWith("var(--pfig-diff-") ? e.style?.stroke : "rgba(212, 228, 255, 0.78)",
          strokeWidth: Math.max(Number(e.style?.strokeWidth) || 2, 3.5),
          filter: EDGE_GLOW_FILTER,
        },
      };
    }
    return { ...e, zIndex: 0 };
  });
}

function Toolbar({
  mode,
  reachableUnavailable,
  onModeChange,
  onRefresh,
  sessionTheme,
  onSessionThemeChange,
  showValueNodes,
  onShowValueNodesChange,
  kwargsMode,
  onKwargsModeChange,
  onExport,
  comparison,
}: {
  comparison?: React.ReactNode;
  mode: string;
  reachableUnavailable: boolean;
  onModeChange: (m: "reachable" | "all") => void;
  onRefresh: () => void;
  sessionTheme: "auto" | GraphExportColorMode;
  onSessionThemeChange: (v: "auto" | GraphExportColorMode) => void;
  showValueNodes: boolean;
  onShowValueNodesChange: (v: boolean) => void;
  kwargsMode: KwargMode;
  onKwargsModeChange: (mode: KwargMode) => void;
  onExport: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 12,
        padding: "6px 10px",
        borderBottom: "1px solid var(--pfig-border, #333)",
        background: "var(--pfig-bar, #1e1e1e)",
        fontSize: 12,
      }}
    >
      <span style={{ fontWeight: 600 }}>pfig graph</span>
      {comparison}
      <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ opacity: 0.88, whiteSpace: "nowrap" }}>Scope</span>
        <select
          value={mode === "reachable" ? "reachable" : "all"}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "reachable" || v === "all") {
              onModeChange(v);
            }
          }}
          aria-label="Graph scope: bindings reachable from entrypoint or all bindings"
          style={TOOLBAR_SELECT_STYLE}
        >
          <option value="reachable">From entrypoint</option>
          <option value="all">All bindings</option>
        </select>
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ opacity: 0.88, whiteSpace: "nowrap" }}>Values</span>
        <select
          value={showValueNodes ? "separate" : "compact"}
          onChange={(e) => {
            const v = e.target.value;
            onShowValueNodesChange(v === "separate");
          }}
          aria-label="Value bindings: compact folded kwargs or separate value cards"
          title="Compact folds leaf value bindings into @binding = preview on kwargs and re-layouts."
          style={TOOLBAR_SELECT_STYLE}
        >
          <option value="compact">Compact</option>
          <option value="separate">Separate cards</option>
        </select>
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
        Kwargs
        <select
          aria-label="Kwargs"
          value={kwargsMode}
          onChange={(e) => onKwargsModeChange(e.target.value === "expanded" ? "expanded" : "collapsed")}
          style={TOOLBAR_SELECT_STYLE}
        >
          <option value="collapsed">Collapsed</option>
          <option value="expanded">Expanded</option>
        </select>
      </label>
      {reachableUnavailable ? (
        <span style={{ color: "var(--pfig-warn, #c9a227)" }}>no entrypoint — showing all</span>
      ) : null}
      <div
        style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ opacity: 0.88, whiteSpace: "nowrap" }}>Theme</span>
          <select
            value={sessionTheme}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "auto") {
                onSessionThemeChange("auto");
              } else if (v === "light" || v === "dark") {
                onSessionThemeChange(v);
              }
            }}
            aria-label="Graph theme: follow settings or force light or dark"
            style={TOOLBAR_SELECT_STYLE}
          >
            <option value="auto">Auto (settings)</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </label>
        <button type="button" onClick={onRefresh}>
          Refresh
        </button>
        <button type="button" onClick={onExport}>
          Export PNG
        </button>
      </div>
    </div>
  );
}

function FitView({ tick, disabled }: { tick: number; disabled: boolean }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (disabled) return;
    const t = setTimeout(() => fitView({ padding: 0.2, duration: 200 }), 80);
    return () => clearTimeout(t);
  }, [tick, disabled, fitView]);
  return null;
}

type GraphWebviewState = {
  showValueNodes?: boolean;
  /** Legacy: inverted semantics; migrated once to `showValueNodes`. */
  foldValueNodes?: boolean;
};

export function GraphApp() {
  const host = useMemo(() => createGraphHostTransport(), []);
  const [vm, setVm] = useState<GraphViewModel | null>(null);
  const [showValueNodes, setShowValueNodes] = useState(false);
  const [mode, setMode] = useState<"reachable" | "all">("reachable");
  const [{ tick: layoutTick, fit: fitLayout }, setLayoutStatus] = useState({ tick: 0, fit: false });
  const viewportRequest = useRef<{ kind: "anchor"; id: string } | { kind: "fit" | "preserve" } | null>(null);
  const [visibleRemoved, setVisibleRemoved] = useState<ReadonlySet<string>>(() => new Set());
  const [showAllOldWires, setShowAllOldWires] = useState(false);
  const [solo, setSolo] = useState<SoloTarget | null>(null);
  const [viewChoice, setViewChoice] = useState<GraphView | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<ReadonlySet<string>>(() => new Set());
  const [kwargsMode, setKwargsMode] = useState<KwargMode>("collapsed");
  const [kwargOverrides, setKwargOverrides] = useState<ReadonlySet<string>>(() => new Set());
  const kwargs = useMemo<KwargDisplay>(() => ({
    isCollapsed: (n) => canCollapseKwargs(n) && ((kwargsMode === "collapsed") !== kwargOverrides.has(n.id)),
    toggle: (id) => {
      viewportRequest.current = { kind: "anchor", id };
      setKwargOverrides((prev) => {
        const next = new Set(prev);
        if (!next.delete(id)) next.add(id);
        return next;
      });
    },
  }), [kwargsMode, kwargOverrides]);
  const collapseGroup = useCallback((id: string) => {
    viewportRequest.current = { kind: "anchor", id };
    setSolo((prev) => prev?.kind === "file" && prev.id === id ? null : prev);
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);
  const { fitView, getViewport, setViewport } = useReactFlow();
  const [navigationTarget, setNavigationTarget] = useState<{ id: string; fill?: boolean; frameIds?: string[] } | null>(null);
  const [revealedValueId, setRevealedValueId] = useState<string | null>(null);
  const navigationLayoutTick = useRef<number | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const expandGroup = useCallback((id: string) => {
    viewportRequest.current = { kind: "anchor", id };
    setNodes((prev) => prev.map((node) => {
      const selected = node.id === id;
      return Boolean(node.selected) === selected ? node : { ...node, selected };
    }));
    setExpandedGroups((prev) => new Set(prev).add(id));
  }, [setNodes]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<BindingEdgeDetails[]>([]);
  const [edgeHighlight, setEdgeHighlight] = useState<EdgeHoverHighlight>(null);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [exportingPng, setExportingPng] = useState(false);
  const [uiColorMode, setUiColorMode] = useState<GraphExportColorMode>(
    host.defaultColorMode
  );
  /** null = follow extension / `pfig.graph.colorMode` (and editor theme when applicable). */
  const [sessionThemeOverride, setSessionThemeOverride] = useState<
    GraphExportColorMode | null
  >(null);
  const [captureColorMode, setCaptureColorMode] = useState<GraphExportColorMode | null>(null);
  const sessionThemeOverrideRef = useRef<GraphExportColorMode | null>(null);
  sessionThemeOverrideRef.current = sessionThemeOverride;

  const baseDisplayColorMode = sessionThemeOverride ?? uiColorMode;
  const effectiveColorMode = captureColorMode ?? baseDisplayColorMode;

  const runExportRef = useRef<((opts: ExportPngOpts) => Promise<void>) | null>(null);
  const nodesRef = useRef<Node[]>(nodes);
  nodesRef.current = nodes;
  const vmRef = useRef<GraphViewModel | null>(vm);
  vmRef.current = vm;
  const exportLockRef = useRef(false);
  const graphMountRef = useRef<HTMLDivElement | null>(null);
  const measureLayerRef = useRef<HTMLDivElement | null>(null);
  const layoutGenerationRef = useRef(0);
  const [layoutProbe, setLayoutProbe] = useState<LayoutProbe | null>(null);
  const [layoutError, setLayoutError] = useState<string | null>(null);

  useLayoutEffect(() => {
    const s = host.getState();
    if (typeof s?.showValueNodes === "boolean") {
      setShowValueNodes(s.showValueNodes);
    } else if (typeof s?.foldValueNodes === "boolean") {
      setShowValueNodes(!s.foldValueNodes);
    }
    if (s && "exactNamespaceGraph" in s) {
      const { exactNamespaceGraph: _removed, ...rest } = s as GraphWebviewState & {
        exactNamespaceGraph?: boolean;
      };
      host.setState(rest);
    }
  }, [host]);

  const compactProposed = useMemo(() => vm && transformCompactNamespaces(vm, true), [vm]);
  const compactVm = useMemo(() => {
    if (!vm || (!visibleRemoved.size && !showAllOldWires)) return compactProposed;
    return transformCompactNamespaces(withRemovalOverlay(vm, visibleRemoved, showAllOldWires), true);
  }, [vm, compactProposed, visibleRemoved, showAllOldWires]);
  const defaultView = (compactProposed?.nodes.length ?? 0) >= OVERVIEW_THRESHOLD ? "overview" : "bindings";
  const focusId = solo?.kind === "binding" ? solo.id : null;
  const fileSolo = solo?.kind === "file" ? solo : null;
  const fileId = fileSolo?.id ?? null;
  const soloDirection = solo?.direction ?? null;
  const focusNode = focusId && compactVm?.ok ? findNavigationNode(compactVm.nodes, focusId) : null;
  const anchorId = focusNode?.id ?? null;
  const baseView = viewChoice ?? defaultView;
  const overview = anchorId === null && baseView === "overview";
  const sourceUris = useMemo(() => compactVm ? graphSourceUris(compactVm) : [], [compactVm]);

  const foldedVm = useMemo(
    () => compactVm && transformFoldValueNodes(compactVm, !showValueNodes, anchorId ?? revealedValueId),
    [compactVm, showValueNodes, anchorId, revealedValueId]
  );
  const displayVm = useMemo(
    () => foldedVm && anchorId && soloDirection ? focusGraph(foldedVm, anchorId, soloDirection) : foldedVm,
    [foldedVm, anchorId, soloDirection]
  );
  const fullOverview = useMemo(
    () => overview && displayVm ? buildGraphOverview(displayVm, expandedGroups, sourceUris) : null,
    [overview, displayVm, expandedGroups, sourceUris]
  );
  const overviewGraph = useMemo(
    () => fullOverview && displayVm && fileSolo
      ? soloGraphOverview(fullOverview, displayVm, fileSolo.id, fileSolo.direction)
      : fullOverview,
    [fullOverview, displayVm, fileSolo]
  );
  const soloedFileId = overviewGraph !== fullOverview ? fileId : null;
  const layoutScope = useMemo(
    () => ({ vm, overview, anchorId, soloedFileId, soloDirection }),
    [vm, overview, anchorId, soloedFileId, soloDirection]
  );
  const lastLayoutScope = useRef<typeof layoutScope | null>(null);

  useEffect(() => {
    if (fullOverview && fileId !== null && soloedFileId === null) setSolo(null);
  }, [fullOverview, fileId, soloedFileId]);

  useEffect(() => {
    if (focusId !== null && compactVm?.ok && anchorId === null) setSolo(null);
  }, [focusId, compactVm, anchorId]);

  const changeSolo = useCallback((target: SoloTarget | null, toggle = false) => {
    viewportRequest.current = null;
    setNavigationTarget(null);
    setEdgeHighlight(null);
    setHoveredEdgeId(null);
    setSolo((current) =>
      toggle && current?.kind === target?.kind && current?.id === target?.id &&
        current?.direction === target?.direction ? null : target
    );
  }, []);

  const expandRemovalGroups = useCallback((items: readonly GraphNodeVM[]) => {
    if (baseView !== "overview") return;
    setExpandedGroups((current) => new Set([...current, ...items.map(overviewGroupId)]));
  }, [baseView]);

  const locateRemoval = useCallback((id: string) => {
    if (!vm?.diff) return;
    const branch = locateRemovedBranch(vm.diff, id);
    if (!branch.size) return;
    const visible = new Set([...visibleRemoved, ...branch]);
    const overlay = withRemovalOverlay(vm, visible, showAllOldWires);
    const frame = new Set(branch);
    for (const e of overlay.edges) {
      if (e.diffStatus === "removed" && branch.has(e.source)) frame.add(e.target);
    }
    changeSolo(null);
    setVisibleRemoved(visible);
    expandRemovalGroups(overlay.nodes.filter((n) => frame.has(n.id)));
    setNavigationTarget({ id, frameIds: [...frame] });
  }, [vm, visibleRemoved, showAllOldWires, changeSolo, expandRemovalGroups]);

  const hideRemoval = useCallback((id: string) => {
    if (!vm?.diff) return;
    const branch = removedBranch(vm.diff, id);
    viewportRequest.current = { kind: "preserve" };
    setNavigationTarget(null);
    setVisibleRemoved((current) => new Set([...current].filter((key) => !branch.has(key))));
  }, [vm]);

  const showRemovals = useCallback(() => {
    if (!vm?.diff) return;
    viewportRequest.current = { kind: "preserve" };
    setNavigationTarget(null);
    const visible = new Set(vm.diff.baseOnlyNodes.map((n) => n.id));
    const overlay = withRemovalOverlay(vm, visible, true);
    const relevant = new Set(visible);
    for (const edge of overlay.edges) {
      if (edge.diffStatus === "removed") {
        relevant.add(edge.source);
        relevant.add(edge.target);
      }
    }
    expandRemovalGroups(overlay.nodes.filter((n) => relevant.has(n.id)));
    setVisibleRemoved(visible);
    setShowAllOldWires(true);
  }, [vm, expandRemovalGroups]);

  const hideRemovals = useCallback(() => {
    viewportRequest.current = { kind: "preserve" };
    setNavigationTarget(null);
    setVisibleRemoved(new Set());
    setShowAllOldWires(false);
  }, []);
  const diffActions = useMemo(() => ({
    locate: locateRemoval,
    hide: hideRemoval,
    removedIds: new Set(vm?.diff?.baseOnlyNodes.map((n) => n.id)),
  }), [locateRemoval, hideRemoval, vm]);

  const setAllFilesExpanded = (expanded: boolean) => {
    changeSolo(null);
    viewportRequest.current = { kind: "fit" };
    setExpandedGroups(new Set(expanded ? fullOverview?.groups.map(({ id }) => id) : []));
  };

  const soloBinding = useCallback(
    (id: string) => changeSolo({ kind: "binding", id, direction: "dependencies" }), [changeSolo]
  );
  const toggleBindingSolo = useCallback(
    (id: string, direction: FocusDirection) => changeSolo({ kind: "binding", id, direction }, true), [changeSolo]
  );
  const toggleFileSolo = useCallback((id: string, direction: FocusDirection, expanded: boolean) => {
    if (!expanded) expandGroup(id);
    changeSolo({ kind: "file", id, direction }, true);
  }, [changeSolo, expandGroup]);

  const isSolo = anchorId !== null || soloedFileId !== null;
  useEffect(() => {
    if (!isSolo) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      if (graphMountRef.current?.querySelector(".pfig-node-menu:popover-open")) return;
      event.preventDefault();
      event.stopPropagation();
      changeSolo(null);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [changeSolo, isSolo]);

  const setShowValueNodesPersisted = useCallback(
    (next: boolean) => {
      setShowValueNodes(next);
      setRevealedValueId(null);
      const prev = host.getState() ?? {};
      const { foldValueNodes: _drop, exactNamespaceGraph: _dropNs, ...rest } = prev as GraphWebviewState & {
        exactNamespaceGraph?: boolean;
      };
      host.setState({ ...rest, showValueNodes: next });
    },
    [host]
  );

  const setHighlight = useCallback((h: EdgeHoverHighlight) => {
    setEdgeHighlight(h);
  }, []);

  const activeFile = useMemo(
    () => overview && !exportingPng ? selectedOverviewFile(nodes) : null,
    [overview, exportingPng, nodes]
  );

  const displayNodes = useMemo(() => activeFile ? nodes.map((node) => {
    if (node.id === activeFile.id) {
      return {
        ...node,
        zIndex: SELECTED_FILE_Z.panel,
        className: [node.className, "pfig-active-file"].filter(Boolean).join(" "),
      };
    }
    return activeFile.members.has(node.id) ? { ...node, zIndex: SELECTED_FILE_Z.binding } : node;
  }) : nodes, [nodes, activeFile]);

  const displayEdges = useMemo(
    () => edgesWithHoverGlow(edges, edgeHighlight, hoveredEdgeId, activeFile?.members ?? null, exportingPng),
    [edges, edgeHighlight, hoveredEdgeId, activeFile, exportingPng]
  );

  const inspectedEdge = useMemo(() =>
    edges.find((edge) => edge.id === hoveredEdgeId && edge.data) ??
    edges.find((edge) => edge.selected && edge.data),
    [edges, hoveredEdgeId]
  );

  const glowingNodeIds = useMemo(
    () => computeGlowingNodeIds(edges, edgeHighlight, hoveredEdgeId),
    [edges, edgeHighlight, hoveredEdgeId]
  );

  const highlightCtx = useMemo(
    () => ({ setHighlight, glowingNodeIds }),
    [setHighlight, glowingNodeIds]
  );

  useEffect(() => {
    if (!displayVm) {
      return;
    }
    const gen = ++layoutGenerationRef.current;
    setLayoutError(null);
    const elements = overviewGraph
      ? overviewToFlow(displayVm.nodes, overviewGraph, kwargs, expandGroup, collapseGroup, fileSolo, toggleFileSolo, toggleBindingSolo)
      : {
        nodes: bindingFlowNodes(displayVm.nodes, anchorId, toggleBindingSolo, kwargs, soloDirection),
        edges: bindingFlowEdges(displayVm.nodes, bundleBindingEdges(displayVm.edges), kwargs.isCollapsed),
      };
    const hiddenFileParents = overviewGraph ? null
      : new Map(displayVm.nodes.map((node) => [node.id, overviewGroupId(node)]));
    setLayoutProbe({ ...elements, gen, hiddenFileParents });
  }, [displayVm, anchorId, overviewGraph, kwargs, expandGroup, collapseGroup, fileSolo, soloDirection, toggleFileSolo, toggleBindingSolo]);

  useEffect(() => {
    if (!layoutProbe) {
      return;
    }
    const { nodes: baseNodes, edges: le, gen } = layoutProbe;
    let cancelled = false;

    void (async () => {
      await waitNextPaint();
      if (cancelled || gen !== layoutGenerationRef.current) {
        return;
      }

      let measured = measureBindingCardHeightsFromMeasureLayer(measureLayerRef.current);
      if (measured.size === 0 && baseNodes.length > 0) {
        await waitNextPaint();
        if (cancelled || gen !== layoutGenerationRef.current) {
          return;
        }
        measured = measureBindingCardHeightsFromMeasureLayer(measureLayerRef.current);
      }
      if (cancelled || gen !== layoutGenerationRef.current) {
        return;
      }

      // Compact stubs share a height; cards with inline deltas keep their measured height.
      const isCompactStub = (node: Node<BindingCardData>) => node.type === "binding" &&
        node.data.kwargsCollapsed && node.data.diff?.status !== "modified";
      const collapsedHeight = baseNodes.reduce((height, node) =>
        isCompactStub(node) ? Math.max(height, measured.get(node.id) ?? node.data.estimatedHeight) : height, 0
      );
      const merged = baseNodes.map((node) => {
        if (isCompactStub(node)) {
          return { ...node, data: { ...node.data, estimatedHeight: collapsedHeight, collapsedHeight } };
        }
        const h = measured.get(node.id);
        const fallback =
          (node.data as { estimatedHeight?: number }).estimatedHeight ?? 120;
        return {
          ...node,
          data: { ...node.data, estimatedHeight: h ?? fallback },
        };
      });

      const { hiddenFileParents } = layoutProbe;
      const laid = hiddenFileParents
        ? await layoutBindingsOnly(merged, le, hiddenFileParents)
        : await layoutOverview(merged, le);
      if (cancelled || gen !== layoutGenerationRef.current) {
        return;
      }
      const request = viewportRequest.current;
      viewportRequest.current = null;
      const fit = request?.kind !== "preserve" &&
        (request?.kind === "fit" || lastLayoutScope.current !== layoutScope);
      lastLayoutScope.current = layoutScope;
      if (!fit && request?.kind === "anchor") {
        const viewport = getViewport();
        const anchored = anchorViewport(viewport, nodesRef.current, laid, request.id);
        if (anchored !== viewport) void setViewport(anchored);
      }

      setNodes((current) => {
        const selected = new Set(current.filter((node) => node.selected).map((node) => node.id));
        return laid.map((node) =>
          selected.has(node.id) && node.selectable !== false ? { ...node, selected: true } : node
        );
      });
      setEdges(le);
      setLayoutProbe((p) => (p !== null && p.gen === gen ? null : p));
      setLayoutStatus(({ tick }) => ({ tick: tick + 1, fit }));
      host.postMessage({ type: "layoutDone" });
    })().catch((error: unknown) => {
      if (cancelled || gen !== layoutGenerationRef.current) return;
      viewportRequest.current = null;
      setLayoutError(`Could not arrange graph: ${error instanceof Error ? error.message : String(error)}`);
      setLayoutProbe(null);
    });

    return () => {
      cancelled = true;
    };
  }, [host, layoutProbe, layoutScope, getViewport, setViewport, setEdges, setNodes]);

  useEffect(() => {
    const dispose = host.onMessage((msg) => {
      if (msg?.type === "exportPng") {
        const exportDpi =
          typeof msg.exportDpi === "number" && msg.exportDpi > 0
            ? msg.exportDpi
            : 300;
        const exportMaxEdgePx =
          typeof msg.exportMaxEdgePx === "number" && msg.exportMaxEdgePx >= 256
            ? msg.exportMaxEdgePx
            : 8192;
        const fromExtension: GraphExportColorMode =
          msg.colorMode === "light" ? "light" : "dark";
        const colorMode: GraphExportColorMode =
          sessionThemeOverrideRef.current ?? fromExtension;
        void runExportRef.current?.({
          exportDpi,
          exportMaxEdgePx,
          colorMode,
        });
        return;
      }
      if (msg?.type === "graphColorMode") {
        setUiColorMode(msg.colorMode === "light" ? "light" : "dark");
        return;
      }
      if (msg?.type === "graphData") {
        const payload = msg.payload as GraphViewModel;
        setUiColorMode(msg.colorMode === "light" ? "light" : "dark");
        setVm(payload);
        const available = new Set(payload.diff?.baseOnlyNodes.map((n) => n.id));
        setVisibleRemoved((current) => new Set([...current].filter((id) => available.has(id))));
        if (!payload.diff) setShowAllOldWires(false);
        if (payload.mode === "all" || payload.mode === "reachable") {
          setMode(payload.mode === "reachable" ? "reachable" : "all");
        }
      }
    });
    host.ready();
    return dispose;
  }, [host]);

  useEffect(() => {
    if (!navigationTarget || layoutProbe) return;
    const { id: requested, fill, frameIds = [requested] } = navigationTarget;
    const id = findNavigationNode(compactVm?.nodes ?? [], requested)?.id ?? requested;
    if (!nodes.some((n) => n.id === id)) return;
    navigationLayoutTick.current = layoutTick;
    // Let React Flow measure newly expanded cards before fitting the target.
    const timer = setTimeout(() => {
      setNodes((current) => current.map((n) => ({ ...n, selected: n.id === id && n.selectable !== false })));
      const targets = frameIds.map((nodeId) => ({ id: findNavigationNode(compactVm?.nodes ?? [], nodeId)?.id ?? nodeId }));
      void fitView({ nodes: targets, padding: 0.2, duration: 250, maxZoom: fill ? MAX_NODE_ZOOM : 2 });
      setNavigationTarget(null);
    }, 80);
    return () => clearTimeout(timer);
  }, [navigationTarget, nodes, layoutProbe, layoutTick, fitView, setNodes, compactVm]);

  const singleNodeClick = useNodeSingleClick();
  const onNodeClick = useCallback(
    (event: React.MouseEvent, node: Node) => {
      if (node.type !== "binding" || !compactVm) return;
      const reference = event.target instanceof Element
        ? event.target.closest<HTMLElement>("[data-graph-target]")?.dataset.graphTarget
        : undefined;
      const targetId = reference ?? node.id;
      const target = findNavigationNode(compactVm.nodes, targetId);
      if (!target) return;
      if (node.data.diff?.status !== "removed" && target.diff?.status !== "removed" && host.canRevealSource()) {
        const definition = target.activeDefinition;
        if (definition) {
          const reveal = () => host.reveal(definition.uri, definition.range);
          if (reference) reveal();
          else singleNodeClick(event, reveal);
        }
        return;
      }
      if (!reference) return; // Selection does not navigate.

      viewportRequest.current = null;
      const groupId = overviewGroupId(target);
      // If the target is outside the current Solo view, return to the full graph.
      const exitBindingSolo = anchorId !== null && !displayVm?.nodes.some((n) => n.id === target.id);
      const exitFileSolo = soloedFileId !== null && !overviewGraph?.groups.some(({ id }) => id === groupId);
      if (exitBindingSolo || exitFileSolo) changeSolo(null);

      if (overview || (exitBindingSolo && baseView === "overview")) {
        setExpandedGroups((current) => current.has(groupId) ? current : new Set(current).add(groupId));
      }
      if (!foldedVm?.nodes.some((n) => n.id === target.id)) {
        setRevealedValueId(target.id);
      }
      setNavigationTarget({ id: target.id });
    },
    [host, compactVm, overview, anchorId, displayVm, soloedFileId, overviewGraph, changeSolo, baseView, foldedVm, singleNodeClick]
  );

  const onNodeDoubleClick = useCallback((event: ReactMouseEvent, node: Node) => {
    if (node.type !== "binding" && node.type !== "sourceGroup") return;
    if (event.target instanceof Element &&
      event.target.closest(".pfig-node-toolbar, [data-graph-target], .react-flow__handle, .pfig-diff-action")) return;
    viewportRequest.current = null;
    setNavigationTarget({ id: node.id, fill: true });
  }, []);

  const onModeChange = useCallback(
    (m: "reachable" | "all") => {
      changeSolo(null);
      setRevealedValueId(null);
      setMode(m);
      host.requestGraph(m);
    },
    [host, changeSolo]
  );

  const onRefresh = useCallback(() => {
    host.requestGraph(mode);
  }, [host, mode]);

  const onExport = useCallback(() => {
    const colorMode = sessionThemeOverrideRef.current ?? uiColorMode;
    void runExportRef.current?.(defaultExportOptions(colorMode));
  }, [uiColorMode]);

  const clearEdgeHighlights = useCallback(() => {
    setEdgeHighlight(null);
    setHoveredEdgeId(null);
  }, []);

  /**
   * Pointer leave on nodes/edges is not reliable when the cursor moves quickly (events can be
   * skipped or reordered). Sync highlight state to what is actually under the pointer.
   */
  useEffect(() => {
    let raf = 0;
    let cx = 0;
    let cy = 0;

    const flush = () => {
      raf = 0;
      if (exportLockRef.current) {
        return;
      }
      const top = document.elementFromPoint(cx, cy);
      // The file body is translucent. Skip its surface and wrapper, but stop at
      // actual cards, headers, and controls that cover the edge.
      const fileSurface = ".pfig-overview-card--expanded, .pfig-expanded-file-frame";
      const target = top?.matches(fileSurface)
        ? document.elementsFromPoint(cx, cy).find((el) => !el.matches(fileSurface))
        : top;
      // Elevating an edge moves it to another SVG, so hit-test instead of using mouseleave.
      const edge = target?.closest(".react-flow__edge");
      const testId = edge?.getAttribute("data-testid");
      setHoveredEdgeId(testId?.startsWith("rf__edge-") ? testId.slice("rf__edge-".length) : null);
      if (!target?.closest(".react-flow__node")) setEdgeHighlight(null);
    };

    const onMove = (e: PointerEvent) => {
      cx = e.clientX;
      cy = e.clientY;
      if (raf === 0) {
        raf = requestAnimationFrame(flush);
      }
    };

    const onBlur = () => {
      setEdgeHighlight(null);
      setHoveredEdgeId(null);
    };

    document.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("pointermove", onMove);
      window.removeEventListener("blur", onBlur);
      if (raf !== 0) cancelAnimationFrame(raf);
    };
  }, []);

  const error = vm && !vm.ok ? vm.error : layoutError;
  const loadingMessage = vm === null
    ? "Loading graph…"
    : vm.ok && layoutTick === 0 && !layoutError ? "Arranging graph…" : null;

  return (
    <div
      className={`pfig-graph-root pfig-graph-root--${effectiveColorMode}`}
      style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100%" }}
    >
      <Toolbar
        comparison={vm?.diff ? <GraphDiffControls diff={vm.diff} visible={visibleRemoved}
          showingAll={showAllOldWires} show={showRemovals} hide={hideRemovals} locate={locateRemoval} /> : undefined}
        mode={mode}
        reachableUnavailable={vm?.reachableUnavailable ?? false}
        onModeChange={onModeChange}
        onRefresh={onRefresh}
        sessionTheme={sessionThemeOverride === null ? "auto" : sessionThemeOverride}
        onSessionThemeChange={(v) => {
          setSessionThemeOverride(v === "auto" ? null : v);
        }}
        showValueNodes={showValueNodes}
        onShowValueNodesChange={setShowValueNodesPersisted}
        kwargsMode={kwargsMode}
        onKwargsModeChange={(mode) => {
          setKwargsMode(mode);
          setKwargOverrides(new Set());
        }}
        onExport={onExport}
      />
      {vm?.ok ? (
        <GraphFocusBar
          nodes={compactVm?.nodes ?? []}
          anchorLabel={focusNode?.primaryPathId ?? anchorId}
          onFocus={soloBinding}
          viewControls={(
            <>
              <label>
                View{" "}
                <select
                  value={baseView}
                  onChange={(e) => {
                    const view = e.target.value === "overview" ? "overview" : "bindings";
                    changeSolo(null);
                    setViewChoice(view);
                  }}
                >
                  <option value="overview">Overview</option>
                  <option value="bindings">Bindings only</option>
                </select>
              </label>
              {overviewGraph?.openGroups.length ? (
                <select
                  aria-label="Collapse a group"
                  value=""
                  onChange={(e) => collapseGroup(e.target.value)}
                >
                  <option value="" disabled>Collapse…</option>
                  {overviewGraph.openGroups.map(({ id, label }) => (
                    <option key={id} value={id}>{label}</option>
                  ))}
                </select>
              ) : null}
              {fullOverview ? (
                <>
                  <button
                    type="button"
                    title="Expand all files"
                    disabled={!isSolo && !fullOverview.groups.some((group) => !group.expanded)}
                    onClick={() => setAllFilesExpanded(true)}
                  >
                    Expand all
                  </button>
                  <button
                    type="button"
                    title="Collapse all files"
                    disabled={expandedGroups.size === 0}
                    onClick={() => setAllFilesExpanded(false)}
                  >
                    Collapse all
                  </button>
                </>
              ) : null}
            </>
          )}
        />
      ) : null}
      {error ? (
        <div role="alert" style={{ padding: 8, background: "#5a1d1d", color: "#fcc" }}>{error}</div>
      ) : null}
      <div
        ref={graphMountRef}
        className={exportingPng ? "pfig-graph-exporting" : undefined}
        style={{ flex: 1, position: "relative" }}
        onPointerLeave={(e) => {
          const rt = e.relatedTarget;
          if (rt instanceof Node && e.currentTarget.contains(rt)) return;
          clearEdgeHighlights();
        }}
      >
        {loadingMessage ? (
          <div className="pfig-graph-loading" role="status">
            <span className="pfig-graph-spinner" aria-hidden="true" />
            {loadingMessage}
          </div>
        ) : null}
        <GraphDiffActions.Provider value={diffActions}>
        <GraphHighlightContext.Provider value={highlightCtx}>
          {layoutProbe ? (
            <div
              ref={measureLayerRef}
              aria-hidden
              style={{
                position: "fixed",
                left: -20000,
                top: 0,
                visibility: "hidden",
                pointerEvents: "none",
                width: 480,
              }}
            >
              {layoutProbe.nodes.map((node) => (
                <div key={node.id} {...{ [PFIG_MEASURE_ID_ATTR]: node.id }}>
                  {node.type === "sourceGroup"
                    ? <OverviewGroupNode data={node.data as OverviewCardData} />
                    : <BindingNode data={node.data as BindingCardData} />}
                </div>
              ))}
            </div>
          ) : null}
          <ReactFlow
            nodes={displayNodes}
            edges={displayEdges}
            elevateNodesOnSelect={!overview}
            deleteKeyCode={null}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onNodeDoubleClick={onNodeDoubleClick}
            zoomOnDoubleClick={false}
            onPaneClick={clearEdgeHighlights}
            nodeTypes={nodeTypes}
            minZoom={0.05}
            maxZoom={MAX_NODE_ZOOM}
            proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{ interactionWidth: 22 }}
          >
            {exportingPng ? null : (
              <Background
                gap={16}
                color={effectiveColorMode === "light" ? "#bbb" : "#444"}
              />
            )}
            {exportingPng ? null : <Controls />}
            {!exportingPng && inspectedEdge?.data ? (
              <EdgeReferenceDetails details={inspectedEdge.data} selected={Boolean(inspectedEdge.selected)} />
            ) : null}
            {!exportingPng ? (
              <Panel position="bottom-right">
                <GraphLegend overview={overview} />
              </Panel>
            ) : null}
            <FitView
              tick={layoutTick}
              disabled={!fitLayout || !!layoutProbe || navigationTarget !== null || navigationLayoutTick.current === layoutTick}
            />
            <PngExportController
              runExportRef={runExportRef}
              nodesRef={nodesRef}
              setNodes={setNodes}
              vmRef={vmRef}
              vscode={host}
              clearHighlights={clearEdgeHighlights}
              setExportingPng={setExportingPng}
              setCaptureColorMode={setCaptureColorMode}
              exportLockRef={exportLockRef}
              overview={overview}
            />
          </ReactFlow>
        </GraphHighlightContext.Provider>
        </GraphDiffActions.Provider>
      </div>
    </div>
  );
}
