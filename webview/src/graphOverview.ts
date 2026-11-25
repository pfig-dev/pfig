import { bundleBindingEdges, describeBindingEdge, type BindingEdgeDetails, type BindingEdgeBundle } from "./graphEdgeBundles";
import { focusNodeIds, type FocusDirection } from "./graphFocus";
import { FRESH_BASE_PORT, type DiffCounts, type GraphEdgeVM, type GraphNodeVM, type GraphViewModel } from "./types";
import { folderAccentsByFile, type FolderAccent } from "./graphFolderColors";
import { changedArgumentPorts } from "./graphPlanDiff";

export interface OverviewGroup {
  id: string;
  label: string;
  kind: "file" | "unlocated";
  entryFile: boolean;
  folderAccent?: FolderAccent;
  expanded: boolean;
  cardCount: number;
  overrideCount: number;
  diffCounts?: DiffCounts;
}

type GroupLabel = Pick<OverviewGroup, "id" | "label">;

export interface GraphOverview {
  groups: OverviewGroup[];
  openGroups: GroupLabel[];
  bindings: GraphNodeVM[];
  bindingParents: Map<string, string>;
  links: OverviewLink[];
}

export interface OverviewLink {
  source: string;
  target: string;
  count: number;
  /** Retain exact inputs when both bindings are visible, or for a visible fresh instance's base link. */
  bundle?: BindingEdgeBundle;
  details?: BindingEdgeDetails[];
  diffStatus?: GraphEdgeVM["diffStatus"];
}

export const OVERVIEW_LEGEND = "Numbers count references bundled through collapsed files.";

function fileUri(node: GraphNodeVM) {
  return node.comparisonGroupUri ?? node.sourceFileUri ?? node.activeDefinition?.uri ?? null;
}

/** Use the authored file, not the active override site, as the binding's container. */
export function overviewGroupId(node: GraphNodeVM): string {
  const uri = fileUri(node);
  return uri ? `file:${uri}` : "group:unlocated";
}

function shortPath(path: readonly string[]): string {
  return path.map((part) => {
    try {
      return decodeURIComponent(part);
    } catch {
      return part;
    }
  }).join("/");
}

function relativePaths(uris: readonly string[]): Map<string, string[]> {
  const parts = uris.map((uri) => uri.split("/"));
  let prefix = 0;
  while (parts.length && parts.every((p) => p.length > prefix + 1 && p[prefix] === parts[0][prefix])) {
    prefix++;
  }
  return new Map(uris.map((uri, i) => [uri, parts[i].slice(prefix)]));
}

/** Group visible bindings by file without changing their dependency graph. */
export function buildGraphOverview(
  vm: GraphViewModel,
  expanded: ReadonlySet<string>,
  sourceUris: readonly string[],
): GraphOverview {
  const entryUri = vm.entryFileUri ?? vm.nodes.find((n) => n.flags?.isEntrypoint)?.activeDefinition?.uri;
  const files = [...new Set([...sourceUris, ...(entryUri ? [entryUri] : [])])];
  const paths = relativePaths(files);
  const accents = folderAccentsByFile(files);
  const groups = new Map<string, OverviewGroup>();
  const bindings: GraphNodeVM[] = [];
  const bindingParents = new Map<string, string>();
  const visibleIdByBinding = new Map<string, string>();

  function groupFor(node: GraphNodeVM): Pick<OverviewGroup, "id" | "label" | "kind" | "entryFile" | "folderAccent"> {
    const uri = fileUri(node);
    const id = overviewGroupId(node);
    if (!uri) return { id, label: "No source file", kind: "unlocated", entryFile: false };

    return {
      id,
      label: shortPath(paths.get(uri) ?? [uri]),
      kind: "file",
      entryFile: uri === entryUri,
      folderAccent: accents.get(uri),
    };
  }

  for (const node of vm.nodes) {
    const group = groupFor(node);
    const summary = groups.get(group.id) ?? {
      ...group, expanded: expanded.has(group.id), cardCount: 0, overrideCount: 0,
      diffCounts: vm.diff?.byFile[group.id],
    };
    summary.cardCount++;
    if (node.flags?.isOverridden) summary.overrideCount++;
    groups.set(group.id, summary);

    if (summary.expanded) {
      bindings.push(node);
      bindingParents.set(node.id, group.id);
    }
    visibleIdByBinding.set(node.id, summary.expanded ? node.id : group.id);
  }

  const changedPorts = new Map(bindings.filter((n) => n.diff?.fields.length)
    .map((n) => [n.id, changedArgumentPorts(n)]));
  const nodeById = new Map(vm.nodes.map(n => [n.id, n]));
  const bundled = new Map<string, OverviewLink & { details: BindingEdgeDetails[] }>();
  const links: OverviewLink[] = [];
  for (const bundle of bundleBindingEdges(vm.edges)) {
    const { edge, count } = bundle;
    const source = visibleIdByBinding.get(edge.source);
    const target = visibleIdByBinding.get(edge.target);
    if (!source || !target) continue;
    const showsBase = target === edge.target && edge.targetPort === FRESH_BASE_PORT && edge.refKind === "fresh";
    // Old wires require actual cards at both ends, not collapsed file substitutes.
    if (edge.diffStatus === "removed" && (source !== edge.source || target !== edge.target)) continue;
    const showsChange = target === edge.target && changedPorts.get(target)?.has(edge.targetPort);
    if ((source === edge.source && target === edge.target) || showsBase || showsChange) {
      links.push({ source, target, count, bundle });
    } else if (source !== target) {
      const key = JSON.stringify([source, target, edge.diffStatus]);
      const link = bundled.get(key);
      const detail = describeBindingEdge(bundle, nodeById);
      if (link) {
        link.count += count;
        link.details.push(detail);
      } else {
        bundled.set(key, { source, target, count, details: [detail], diffStatus: edge.diffStatus });
      }
    }
  }
  return {
    groups: [...groups.values()],
    openGroups: [...groups.values()]
      .filter(({ expanded }) => expanded)
      .map(({ id, label }) => ({ id, label }))
      .sort((a, b) => a.label.localeCompare(b.label)),
    bindings,
    bindingParents,
    links: [...links, ...bundled.values()],
  };
}

/** Walk from every binding in the file before grouping; bundling can create artificial cycles. */
export function soloGraphOverview(
  overview: GraphOverview, vm: GraphViewModel, groupId: string | null, direction: FocusDirection,
): GraphOverview {
  if (groupId === null || !overview.groups.some(({ id }) => id === groupId)) return overview;

  const owner = (id: string) => overview.bindingParents.get(id) ?? id;
  const filesByBinding = new Map(vm.nodes.map((node) => [node.id, overviewGroupId(node)]));
  const anchors = vm.nodes.filter(({ id }) => filesByBinding.get(id) === groupId).map(({ id }) => id);
  const connected = focusNodeIds(vm.edges, anchors, direction);
  const visible = new Set([...connected].map((id) => filesByBinding.get(id)));
  const bindings = overview.bindings.filter(({ id }) => visible.has(owner(id)));
  return {
    groups: overview.groups.filter(({ id }) => visible.has(id)),
    openGroups: overview.openGroups.filter(({ id }) => visible.has(id)),
    bindings,
    bindingParents: new Map(bindings.map(({ id }) => [id, owner(id)])),
    links: overview.links.filter(({ source, target }) => visible.has(owner(source)) && visible.has(owner(target))),
  };
}

export function graphSourceUris(vm: GraphViewModel): string[] {
  return [...new Set(vm.nodes.map(fileUri).filter((uri): uri is string => uri !== null))];
}
