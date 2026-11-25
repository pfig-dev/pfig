export type RefKind = "shared" | "fresh";

export const FRESH_BASE_PORT = "__fresh_base__";

export interface RangeJson {
  start: { line: number; character: number };
  end: { line: number; character: number };
}

export interface KwargVM {
  name: string;
  valueKind: string;
  inlinePreview?: string | null;
  /** Result of a numeric expression using only effective literal bindings. */
  evaluatedPreview?: string;
  /** Graph node id target; may be canonical after compact-namespace merge. */
  refTargetId?: string | null;
  /**
   * Original qualified binding path for `@…` display when `refTargetId` was remapped to a canonical id.
   */
  refQualPath?: string | null;
  overlayKwargs?: KwargVM[] | null;
  syntheticFreshId?: string | null;
  syntheticInlineId?: string | null;
  syntheticCollectionId?: string | null;
  /** Set when a referenced value binding is folded into this kwarg row (`valueKind === "foldedRef"`). */
  foldedTargetId?: string | null;
  foldedDisplayFull?: string | null;
  foldedDisplayShort?: string | null;
}

export interface CollectionSlotVM {
  port: string;
  label: string;
}

export interface FreshInstanceVM {
  baseProviderId: string;
  baseProviderBindingName: string;
  overlayKwargs: KwargVM[];
}

export type ValueSequenceItemVM =
  | { text: string }
  | { callee: string; targetId: string; kwargs: { name: string; text: string }[] };

export interface ValueSequenceVM {
  kind: "list" | "tuple";
  count: number;
  items: ValueSequenceItemVM[];
}

export type DiffStatus = "added" | "modified" | "removed";
export type DiffCounts = Record<DiffStatus, number>;
export type FieldStep = readonly [
  "callable" | "arg" | "kwarg" | "freshBase" | "value" | "item" | "key" | "operator" | "operand",
  string | number,
];
export type FieldSide =
  | { present: false }
  | { present: true; preview: string; title: string; targetId?: string };
export interface FieldDelta {
  path: readonly FieldStep[];
  before: FieldSide;
  after: FieldSide;
  /** Base-only kwarg that the replacement callable's signature rules out. */
  notAccepted?: true;
  nested?: FieldDelta[];
}
export interface NodeDiff {
  status: DiffStatus;
  fields: FieldDelta[];
  ownerId?: string;
  logicalId?: string;
}
export interface GraphComparison {
  baseLabel: string;
  proposedLabel: string;
  namespaceAlignment?: string;
  counts: DiffCounts;
  nodesById: Record<string, NodeDiff>;
  byFile: Record<string, DiffCounts>;
  removals: { id: string; label?: string; aliases: string[]; fileUri: string | null }[];
  baseOnlyNodes: GraphNodeVM[];
  baseOnlyEdges: GraphEdgeVM[];
  contextNodes: GraphNodeVM[];
}

export interface GraphNodeVM {
  id: string;
  /** Display title: leaf name for value/alias/external; full id for providers. Qualified path is beside the outgoing handle. */
  bindingName: string;
  namespace?: string | null;
  kind: string;
  collection?: {
    shape: string;
    slots: CollectionSlotVM[];
  } | null;
  provider: {
    callableName: string;
    callableFqn: string;
    kwargs: KwargVM[];
  } | null;
  freshInstance: FreshInstanceVM | null;
  binding: {
    valuePreview?: string | null;
    valueSequence?: ValueSequenceVM;
    /** Compiler origin shared by all paths to the same effective value. */
    origin?: readonly [source: string, binding: string];
    isExternal?: boolean;
    externalMissing?: boolean;
  } | null;
  activeDefinition: { uri: string; range: RangeJson } | null;
  /** Original source file, before authored overrides. Inline nodes use their owning binding's file. */
  sourceFileUri?: string | null;
  /** Owning source binding followed by the input path to this inline expression. */
  syntheticOrigin?: readonly string[];
  /** Corresponding proposed file group; sourceFileUri retains the actual base file. */
  comparisonGroupUri?: string | null;
  diff?: NodeDiff;
  oldPorts?: CollectionSlotVM[];
  extraRanges?: { providerCallable?: RangeJson };
  flags?: {
    isEntrypoint?: boolean;
    isReachable?: boolean;
    isExternal?: boolean;
    isOverridden?: boolean;
    isAnonymous?: boolean;
  };
  /**
   * Compact namespace mode: sorted qualified paths merged into this card (same `activeDefinition`).
   * Omitted when the node was not merged or has no definition key.
   */
  pathAliases?: string[];
  /** Shortest among `pathAliases` for stable primary labeling. */
  primaryPathId?: string;
}

export interface GraphEdgeVM {
  id: string;
  source: string;
  target: string;
  targetPort: string;
  refKind: RefKind | string;
  /** Original endpoint paths before namespace compaction. */
  sourcePath?: string;
  targetPath?: string;
  diffStatus?: "added" | "removed";
  basePortLabel?: string;
}

export interface GraphViewModel {
  /** Browser server has an editor reveal callback (VS Code uses its own transport). */
  supportsSourceReveal?: boolean;
  diff?: GraphComparison;
  ok: boolean;
  error?: string | null;
  nodes: GraphNodeVM[];
  edges: GraphEdgeVM[];
  mode: string;
  reachableUnavailable?: boolean;
  entrypoint?: string | null;
  entryFileUri?: string | null;
  allBindingCount?: number;
  visibleBindingCount?: number;
  /** Phase 2: include chains per path from LSP (e.g. train → pol → consts). */
  aliasIncludeChains?: Record<string, string[]> | undefined;
}
