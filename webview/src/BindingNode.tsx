import { Fragment, memo, useContext, useLayoutEffect, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Handle, Position, useUpdateNodeInternals } from "reactflow";
import { nodeBadgeLabels } from "./graphNodeBadges";
import { NodeToolbar } from "./NodeToolbar";
import { useNodeSingleClick } from "./useNodeSingleClick";
import type { FocusDirection } from "./graphFocus";
import { BindingValuePreview } from "./BindingValuePreview";
import { useGraphHighlight } from "./graphHighlightContext";
import { resolveNodePalette } from "./graphNodeTheme";
import { BINDING_KW_ROW_H, canCollapseKwargs } from "./graphNodeMetrics";
import { COLLAPSED_PROVIDER_HEIGHT, COLLAPSED_PROVIDER_INPUT, OVERVIEW_GROUP_INPUT } from "./graphBindingConstants";
import { FRESH_BASE_PORT, type DiffStatus, type GraphNodeVM, type KwargVM } from "./types";
import { BindingFieldDelta } from "./BindingFieldDelta";
import { GraphDiffActions } from "./GraphDiffControls";
import { changedArgumentPorts, diffFieldPort, omittedArgumentRows } from "./graphPlanDiff";

const STATUS_LABEL: Record<DiffStatus, string> = { added: "+ Added", modified: "~ Modified", removed: "− Removed" };

const PATH_ALIAS_TRUNC = 36;

/** Qualified path shown beside the outgoing handle (matches merged-alias rows). */
function qualifiedOutgoingPath(n: GraphNodeVM): string {
  return n.primaryPathId ?? n.id;
}

function wrapBindingPath(label: string) {
  const parts = label.split("/");
  return parts.map((part, index) => (
    <Fragment key={index}>
      {part}{index < parts.length - 1 ? <>/<wbr /></> : null}
    </Fragment>
  ));
}

function definitionFileBasename(uri: string | undefined): string | undefined {
  if (!uri) return undefined;
  const path = uri.startsWith("file://") ? uri.slice("file://".length) : uri;
  const parts = path.split(/[/\\]/);
  const last = parts[parts.length - 1];
  return last || undefined;
}

/** Same size and white ring for all left-side kwarg / binding sinks. */
function leftKwargSinkHandleStyle(
  rowCenterPx: number | "center",
  color: string
): CSSProperties {
  const vertical: Pick<CSSProperties, "top" | "transform"> =
    rowCenterPx === "center"
      ? { top: "50%", transform: "translateY(-50%)" }
      : { top: rowCenterPx, transform: "translateY(-50%)" };
  return {
    left: -6,
    position: "absolute",
    ...vertical,
    width: 6,
    height: 6,
    minWidth: 6,
    minHeight: 6,
    boxSizing: "border-box",
    borderRadius: "50%",
    background: color,
    border: "1px solid var(--pfig-handle-ring, rgba(255, 255, 255, 0.92))",
  };
}

const sinkRowHover: CSSProperties = {
  borderRadius: 4,
  marginLeft: -4,
  paddingLeft: 4,
  marginRight: -4,
};

function kwargValueSpans(k: KwargVM) {
  if (k.valueKind === "foldedRef" && k.foldedDisplayShort) {
    return (
      <span
        style={{
          marginLeft: 6,
          color: "var(--pfig-muted)",
          cursor: "pointer",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          minWidth: 0,
          flex: 1,
        }}
        data-graph-target={k.foldedTargetId ?? undefined}
        title={k.foldedDisplayFull ?? undefined}
      >
        {k.foldedDisplayShort}
      </span>
    );
  }
  if (k.valueKind === "nestedFresh") {
    return (
      <span style={{ marginLeft: 6, color: "var(--pfig-muted)", fontStyle: "italic" }}>
        ⟨fresh⟩
      </span>
    );
  }
  if (
    k.valueKind === "freshNested" ||
    k.valueKind === "inlineNested" ||
    k.valueKind === "collectionNested"
  ) {
    return null;
  }
  if (k.refTargetId) {
    const refDisp = k.refQualPath ?? k.refTargetId;
    return (
      <span
        className="pfig-kwarg-value"
        data-graph-target={k.refTargetId}
        title={`@${refDisp}${k.inlinePreview ?? ""}`}
        style={{ color: "var(--pfig-kw-link)", cursor: "pointer" }}
      >
        → @{refDisp}
        {k.inlinePreview ? (
          <span style={{ color: "var(--pfig-kw-link-dim)" }}>{k.inlinePreview}</span>
        ) : null}
      </span>
    );
  }
  if (k.inlinePreview) {
    const result = k.evaluatedPreview;
    const full = result === undefined ? k.inlinePreview : `${k.inlinePreview} = ${result}`;
    return (
      <span className="pfig-kwarg-value pfig-kwarg-preview" title={full}>
        <span className="pfig-kwarg-expression">{k.inlinePreview}</span>
        {result !== undefined ? <span className="pfig-kwarg-result"> = {result}</span> : null}
      </span>
    );
  }
  return null;
}

function kwDisplaySuffix(k: KwargVM) {
  if (k.valueKind === "foldedRef") {
    return kwargValueSpans(k);
  }
  if (k.valueKind === "freshNested") {
    return (
      <span style={{ marginLeft: 6, color: "var(--pfig-muted)", fontStyle: "italic" }}>⟨fresh⟩</span>
    );
  }
  if (k.valueKind === "inlineNested") {
    return (
      <span style={{ marginLeft: 6, color: "var(--pfig-muted)", fontStyle: "italic" }}>⟨inline⟩</span>
    );
  }
  if (k.valueKind === "collectionNested") {
    return null;
  }
  return kwargValueSpans(k);
}

export const BindingNode = memo(function BindingNode({
  id,
  data,
}: {
  id?: string;
  data: GraphNodeVM & {
    kwargPortIds: string[];
    estimatedWidth: number;
    estimatedHeight: number;
    hasGroupedInput?: boolean;
    kwargsCollapsed?: boolean;
    collapsedHeight?: number;
    toggleKwargs?: (id: string) => void;
    soloDirection: FocusDirection | null;
    toggleSolo: (id: string, direction: FocusDirection) => void;
  };
}) {
  const n = data;
  const singleClick = useNodeSingleClick();
  const palette = resolveNodePalette(n);
  const gh = useGraphHighlight();
  const actions = useContext(GraphDiffActions);
  const fields = n.diff?.fields ?? [];
  const kw = n.provider?.kwargs ?? [];
  const overlayKwargs = n.freshInstance?.overlayKwargs ?? [];
  const { removed, notAccepted } = omittedArgumentRows(n);
  const status = n.diff?.status;
  const callable = fields.find((f) => f.path[0][0] === "callable");
  const freshBase = fields.find((f) => f.path[0][0] === "freshBase");
  const value = fields.find((f) => f.path[0][0] === "value");
  // A provider/literal replacement is one change, even though the payload
  // records the callable and literal on separate structural fields.
  const providerToValue = callable?.before.present && !callable.after.present &&
    value?.after.present && !value.before.present;
  const valueToProvider = value?.before.present && !value.after.present &&
    callable?.after.present && !callable.before.present;
  const shownCallable = valueToProvider ? { ...callable, before: value.before } : callable;
  const shownValue = providerToValue ? { ...value, before: callable.before } : valueToProvider ? undefined : value;
  const statusBadge = status ? <span className="pfig-diff-badge">{STATUS_LABEL[status]}</span> : null;
  const hide = status === "removed" && actions ? (
    <button
      type="button"
      title="Hide removed branch"
      aria-label={`Hide removed branch ${n.primaryPathId ?? n.id}`}
      className="pfig-diff-action pfig-diff-hide nodrag nopan"
      onClick={(event) => { event.stopPropagation(); actions.hide(n.diff?.logicalId ?? n.id); }}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") event.stopPropagation(); }}
    >×</button>
  ) : null;
  const updateNodeInternals = useUpdateNodeInternals();
  useLayoutEffect(() => {
    // Refresh handle positions after switching between a shared input and argument ports.
    if (id) updateNodeInternals(id);
  }, [id, n.kwargsCollapsed, n.hasGroupedInput, n.kwargPortIds, updateNodeInternals]);
  const collSlots = n.collection?.slots ?? [];
  const rowH = BINDING_KW_ROW_H;
  const fi = n.freshInstance;
  const badges = nodeBadgeLabels(n.kind, n.flags);

  const isExternal = Boolean(n.flags?.isExternal || n.kind === "external");
  const extMissing = Boolean(n.binding?.externalMissing);
  const preview = shownValue
    ? <BindingFieldDelta field={shownValue} />
    : n.binding?.valuePreview ? <BindingValuePreview binding={n.binding} /> : null;
  const showBindingIn = !n.provider && !fi && !n.collection;
  const defFileSubtitle =
    showBindingIn && n.activeDefinition?.uri
      ? definitionFileBasename(n.activeDefinition.uri)
      : undefined;
  const providerDefFileSubtitle =
    n.provider && n.activeDefinition?.uri
      ? definitionFileBasename(n.activeDefinition.uri)
      : undefined;
  const outgoingPathLabel = qualifiedOutgoingPath(n);
  const outgoingPathShow =
    outgoingPathLabel.length > PATH_ALIAS_TRUNC
      ? `${outgoingPathLabel.slice(0, PATH_ALIAS_TRUNC - 1)}…`
      : outgoingPathLabel;

  const titleStyle: CSSProperties = n.flags?.isAnonymous
    ? { fontStyle: "italic", color: "var(--pfig-faint)" }
    : {};

  const cardTitle = isExternal
    ? extMissing
      ? "External binding — not provided in this config"
      : "External binding (injected at runtime)"
    : n.flags?.isEntrypoint && n.id === "__entrypoint__"
      ? `__entrypoint__ → ${n.bindingName}`
      : undefined;

  const setSourceHover = () => {
    gh?.setHighlight({ kind: "source", nodeId: n.id });
  };
  const setSinkHover = (port: string) => {
    gh?.setHighlight({ kind: "sink", nodeId: n.id, port });
  };
  const onCardPointerLeave = (e: ReactPointerEvent) => {
    const rt = e.relatedTarget;
    if (rt instanceof Element && e.currentTarget.contains(rt)) return;
    gh?.setHighlight(null);
  };

  const nodeConnectionGlow = (gh?.glowingNodeIds?.has(n.id) ?? false)
    ? "0 0 0 1px rgba(130, 175, 255, 0.4), 0 0 28px 10px rgba(107, 158, 255, 0.22), inset 0 0 22px rgba(107, 158, 255, 0.07)"
    : null;
  const boxShadowParts: string[] = [];
  if (nodeConnectionGlow) boxShadowParts.push(nodeConnectionGlow);

  const cardBorder =
    palette.borderStyle === "solid"
      ? `1px solid ${palette.border}`
      : `2px ${palette.borderStyle} ${palette.border}`;

  const collapsible = canCollapseKwargs(n);
  const collapseArgs = collapsible && !n.kwargsCollapsed && n.toggleKwargs ? (
    <button
      type="button"
      className="pfig-kwargs-toggle nodrag nopan"
      title="Hide arguments"
      aria-label={`Collapse arguments for ${outgoingPathLabel}`}
      aria-expanded={true}
      onClick={(event) => {
        event.stopPropagation();
        n.toggleKwargs?.(n.id);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") event.stopPropagation();
      }}
    >
      <span aria-hidden="true">−</span>
    </button>
  ) : null;
  const toolbar = (
    <NodeToolbar
      label={outgoingPathLabel}
      active={n.soloDirection}
      onToggle={(direction) => n.toggleSolo(n.id, direction)}
      collapse={collapseArgs}
    />
  );
  const cardStyle: CSSProperties = {
    position: "relative",
    width: n.estimatedWidth,
    minWidth: n.estimatedWidth,
    maxWidth: n.estimatedWidth,
    boxSizing: "border-box",
    border: cardBorder,
    borderRadius: 6,
    background: `linear-gradient(${palette.background}, ${palette.background}), var(--pfig-body-bg)`,
    color: "var(--pfig-fg, #ccc)",
    fontSize: 12,
    padding: "6px 8px",
    boxShadow: boxShadowParts.length ? boxShadowParts.join(", ") : undefined,
    transition: "box-shadow 0.14s ease-out",
  };

  const argument = (k: KwargVM, overlay = false) => {
    const field = fields.find((f) => diffFieldPort(f) === k.name);
    return (
      <>
        <span className={`pfig-kwarg-name${field ? " pfig-diff-field-name" : ""}`} title={k.name}>{k.name}</span>
        {field ? <BindingFieldDelta field={field} /> : overlay ? kwargValueSpans(k) : kwDisplaySuffix(k)}
      </>
    );
  };
  const inputRow = (port: string, content: ReactNode, historical = false) => (
    <div
      key={port}
      className={historical ? "kw-row pfig-historical-argument" : "kw-row"}
      data-kwarg={port}
      onPointerEnter={() => setSinkHover(port)}
      style={{ display: "flex", alignItems: "center", minHeight: rowH, minWidth: 0, position: "relative", ...sinkRowHover }}
    >
      <Handle type="target" position={Position.Left} id={`kw-${port}`}
        style={leftKwargSinkHandleStyle(rowH / 2, palette.sinkHandle)} />
      {content}
    </div>
  );
  const argumentRow = (k: KwargVM, overlay = false) => inputRow(k.name, argument(k, overlay));
  const historicalRows = (
    rows: typeof removed, label: string, title?: string,
  ) => rows.length > 0 ? (
    <div className="pfig-historical-arguments" role="group" aria-label={label}>
      <div className="pfig-historical-heading" title={title}>{label}</div>
      {rows.map(({ port, field }) => inputRow(port, <>
        <span className="pfig-kwarg-name" title={port}>{port}</span>
        <BindingFieldDelta field={field} />
      </>, true))}
    </div>
  ) : null;
  const omitted = <>
    {historicalRows(removed, "Removed arguments")}
    {notAccepted.length > 0 && n.provider && historicalRows(
      notAccepted,
      `Not accepted by ${callable?.after.present ? callable.after.preview : n.provider.callableName}`,
      n.provider.callableFqn,
    )}
  </>;
  const oldPorts = n.oldPorts?.map(({ port, label }) => (
    <div key={port} className="pfig-diff-old-port" onPointerEnter={() => setSinkHover(port)}>
      <Handle type="target" position={Position.Left} id={`kw-${port}`}
        style={leftKwargSinkHandleStyle("center", palette.sinkHandle)} />
      <span title={label}>{label} (old input)</span>
    </div>
  ));

  if (collapsible && n.kwargsCollapsed) {
    const rows = fi ? overlayKwargs : kw;
    const changedPorts = changedArgumentPorts(n);
    const shownRows = rows.filter((k) => changedPorts.has(k.name));
    const hiddenCount = rows.length - shownRows.length;
    return (
      <div
        className={`binding-node binding-node--${palette.visualKind} pfig-provider-collapsed`}
        data-diff-status={status}
        onClick={(event) => {
          const target = event.target;
          if (target instanceof Element && target.closest("[data-graph-target], .react-flow__handle, .pfig-node-toolbar, .pfig-diff-action")) return;
          singleClick(event, () => n.toggleKwargs?.(n.id));
        }}
        onPointerLeave={onCardPointerLeave}
        style={{ ...cardStyle, minHeight: COLLAPSED_PROVIDER_HEIGHT, height: n.collapsedHeight }}
        title={[outgoingPathLabel, n.activeDefinition?.uri, ...badges].filter(Boolean).join("\n")}
      >
        <Handle
          type="source"
          position={Position.Right}
          id="out"
          title={outgoingPathLabel}
          onPointerEnter={setSourceHover}
          style={{ top: "50%", background: palette.sourceHandle }}
        />
        <div className="pfig-binding-title pfig-node-title" onPointerEnter={setSourceHover}>
          <button
            type="button"
            className="pfig-provider-expand nodrag nopan"
            aria-label={`Expand arguments for ${outgoingPathLabel}`}
            aria-expanded={false}
            title={outgoingPathLabel}
            style={titleStyle}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") event.stopPropagation();
            }}
          >
            {wrapBindingPath(n.bindingName)}
          </button>
          {statusBadge}
          {hide}
          {toolbar}
        </div>
        {fi ? (
          <div className="pfig-collapsed-input-row">
            <Handle
              type="target"
              position={Position.Left}
              id={`kw-${FRESH_BASE_PORT}`}
              title="Base provider"
              onPointerEnter={() => setSinkHover(FRESH_BASE_PORT)}
              style={{ ...leftKwargSinkHandleStyle("center", palette.sinkHandle), left: -11 }}
            />
            {freshBase ? <BindingFieldDelta field={freshBase} /> : (
              <div className="pfig-provider-callable" data-graph-target={fi.baseProviderId}
                style={{ color: palette.accent, cursor: "pointer" }} title={fi.baseProviderId}>
                @{fi.baseProviderBindingName}
              </div>
            )}
          </div>
        ) : (
          <div className="pfig-provider-callable" title={n.provider?.callableFqn}>
            {shownCallable ? <BindingFieldDelta field={shownCallable} /> : n.provider?.callableName}
          </div>
        )}
        {shownRows.length > 0 ? (
          <div className="pfig-collapsed-changes">{shownRows.map((k) => argumentRow(k, Boolean(fi)))}</div>
        ) : null}
        <div className="pfig-collapsed-input-row pfig-hidden-kwargs" onPointerEnter={() => setSinkHover("__collapsed__")}>
          <Handle
            type="target"
            position={Position.Left}
            id={COLLAPSED_PROVIDER_INPUT}
            title="Arguments"
            style={{ ...leftKwargSinkHandleStyle("center", palette.sinkHandle), left: -11 }}
          />
          {hiddenCount > 0 || (shownRows.length === 0 && !removed.length && !notAccepted.length)
            ? `${hiddenCount} ${hiddenCount === 1 ? "argument" : "arguments"} hidden`
            : null}
        </div>
        {omitted}
      </div>
    );
  }

  return (
    <div
      className={`binding-node binding-node--${palette.visualKind}`}
      data-diff-status={status}
      onPointerLeave={onCardPointerLeave}
      style={cardStyle}
      title={cardTitle}
    >
      {n.hasGroupedInput ? (
        <Handle type="target" position={Position.Left} id={OVERVIEW_GROUP_INPUT} className="pfig-group-link-handle" />
      ) : null}
      {n.flags?.isOverridden && n.activeDefinition ? (
        <div className="pfig-override-source" title={n.activeDefinition.uri}>
          Override in {definitionFileBasename(n.activeDefinition.uri)}:{n.activeDefinition.range.start.line + 1}
        </div>
      ) : null}
      {!showBindingIn ? (
        <Handle
          type="source"
          position={Position.Right}
          id="out"
          onPointerEnter={setSourceHover}
          title={outgoingPathLabel}
          style={{ top: 18, background: palette.sourceHandle }}
        />
      ) : null}

      {showBindingIn ? (
        <Handle
          type="target"
          position={Position.Left}
          id="kw-__binding__"
          onPointerEnter={() => setSinkHover("__binding__")}
          style={leftKwargSinkHandleStyle("center", palette.sinkHandle)}
        />
      ) : null}

      {showBindingIn ? (
        <>
          <div onPointerEnter={setSourceHover}>
            <div className="pfig-binding-title pfig-node-title" style={{ marginBottom: defFileSubtitle ? 2 : 4 }}>
              <span title={outgoingPathLabel} style={{ fontWeight: 600, ...titleStyle }}>{wrapBindingPath(n.bindingName)}</span>
              {statusBadge}
              {hide}
              {toolbar}
            </div>
            {defFileSubtitle ? (
              <div
                style={{
                  fontSize: 10,
                  color: "var(--pfig-muted)",
                  marginBottom: 4,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={n.activeDefinition?.uri}
              >
                {defFileSubtitle}
              </div>
            ) : null}
            {badges.length > 0 ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 4 }}>
                {badges.map((t) => (
                  <span
                    key={t}
                    style={{
                      fontSize: 10,
                      padding: "1px 6px",
                      borderRadius: 4,
                      background: "var(--pfig-badge-bg)",
                      color: "var(--pfig-badge-fg)",
                    }}
                  >
                    {t}
                  </span>
                ))}
              </div>
            ) : null}
            <div style={{ marginTop: 4, marginBottom: 2 }}>
              <div
                style={{
                  position: "relative",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "flex-end",
                  minHeight: BINDING_KW_ROW_H,
                  marginBottom: 2,
                  paddingRight: 4,
                }}
              >
                <span
                  style={{
                    fontFamily: "monospace",
                    fontSize: 10,
                    color: "var(--pfig-muted)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                    minWidth: 0,
                    marginRight: 6,
                    textAlign: "right",
                  }}
                  title={outgoingPathLabel}
                >
                  {outgoingPathShow}
                </span>
                <Handle
                  type="source"
                  position={Position.Right}
                  id="out"
                  title={outgoingPathLabel}
                  onPointerEnter={setSourceHover}
                  style={{
                    position: "absolute",
                    right: -6,
                    top: "50%",
                    transform: "translateY(-50%)",
                    background: palette.sourceHandle,
                  }}
                />
              </div>
            </div>
          </div>
          <div
            onPointerEnter={() => setSinkHover("__binding__")}
            style={{ minHeight: preview ? undefined : 20 }}
          >
            {preview}
          </div>
        </>
      ) : (
        <>
          <div onPointerEnter={setSourceHover}>
            <div className="pfig-binding-title pfig-node-title" style={{ marginBottom: providerDefFileSubtitle ? 2 : 4 }}>
              <span title={outgoingPathLabel} style={{ fontWeight: 600, ...titleStyle }}>{wrapBindingPath(n.bindingName)}</span>
              {statusBadge}
              {hide}
              {toolbar}
            </div>
            {providerDefFileSubtitle ? (
              <div
                style={{
                  fontSize: 10,
                  color: "var(--pfig-muted)",
                  marginBottom: 4,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={n.activeDefinition?.uri}
              >
                {providerDefFileSubtitle}
              </div>
            ) : null}
            {badges.length > 0 ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 4 }}>
                {badges.map((t) => (
                  <span
                    key={t}
                    style={{
                      fontSize: 10,
                      padding: "1px 6px",
                      borderRadius: 4,
                      background: "var(--pfig-badge-bg)",
                      color: "var(--pfig-badge-fg)",
                    }}
                  >
                    {t}
                  </span>
                ))}
              </div>
            ) : null}
            {n.provider ? (
              <div
                style={{ fontFamily: "monospace", marginBottom: 6, cursor: "pointer" }}
                className="clickable-callable pfig-wrap-preview"
                title={n.provider.callableFqn}
              >
                {shownCallable ? <BindingFieldDelta field={shownCallable} /> : n.provider.callableName}
              </div>
            ) : null}
          </div>

      {n.collection?.slots?.length ? (
        <div>
          <div
            style={{
              borderTop: "1px solid var(--pfig-divider)",
              paddingTop: 6,
              marginBottom: 2,
            }}
          >
            {collSlots.map((s) => {
              const hid = `kw-${s.port}`;
              return (
                <div
                  key={s.port}
                  className="kw-row"
                  data-kwarg={s.port}
                  onPointerEnter={() => setSinkHover(s.port)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    minHeight: rowH,
                    position: "relative",
                    paddingLeft: 6,
                    ...sinkRowHover,
                  }}
                >
                  <Handle
                    type="target"
                    position={Position.Left}
                    id={hid}
                    style={leftKwargSinkHandleStyle(rowH / 2, palette.sinkHandle)}
                  />
                  <span className="pfig-wrap-preview" style={{ fontFamily: "monospace", color: "var(--pfig-kw-label)" }}>
                    {s.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {n.provider ? (
        <div>
          <div
            style={{
              borderTop: "1px solid var(--pfig-divider)",
              paddingTop: 6,
              marginBottom: 2,
            }}
          >
            {kw.map((k) => argumentRow(k))}
          </div>
        </div>
      ) : null}

      {fi ? (
        <div>
          <div
            onPointerEnter={() => setSinkHover(FRESH_BASE_PORT)}
            style={{
              position: "relative",
              display: "flex",
              alignItems: "center",
              minHeight: rowH,
              paddingLeft: 6,
              fontFamily: "monospace",
              marginBottom: 6,
              ...sinkRowHover,
            }}
          >
            <Handle
              type="target"
              position={Position.Left}
              id={`kw-${FRESH_BASE_PORT}`}
              style={leftKwargSinkHandleStyle(rowH / 2, palette.sinkHandle)}
            />
            {freshBase ? <BindingFieldDelta field={freshBase} /> : (
              <span className="pfig-wrap-preview" data-graph-target={fi.baseProviderId} style={{ color: palette.accent, cursor: "pointer" }} title={fi.baseProviderId}>
                @{fi.baseProviderBindingName}
              </span>
            )}
          </div>
          {overlayKwargs.length ? (
            <div
              style={{
                borderTop: "1px solid var(--pfig-divider)",
                paddingTop: 6,
                marginBottom: 2,
              }}
            >
              {overlayKwargs.map((k) => argumentRow(k, true))}
            </div>
          ) : null}
        </div>
      ) : null}

        </>
      )}

      {!showBindingIn ? preview : null}
      {!n.provider && callable && !providerToValue ? <BindingFieldDelta field={callable} /> : null}
      {!fi && freshBase ? <BindingFieldDelta field={freshBase} /> : null}
      {omitted}
      {oldPorts}
    </div>
  );
});
