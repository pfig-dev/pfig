import { BINDING_NODE_WIDTH, COLLAPSED_PROVIDER_HEIGHT } from "./graphBindingConstants";
import type { GraphNodeVM, KwargVM, ValueSequenceVM } from "./types";

/** Top + bottom padding from `padding: 6px 8px` on the card. */
const BINDING_CARD_PADDING_Y = 12;

/** Title + badges + callable stub before the provider/collection/fresh body (see BindingNode). */
const BINDING_HEADER_CONTENT_H = 36;

export const BINDING_KW_ROW_H = 22;


/** Bottom breathing room inside the card (BindingNode `+ 12`). */
const BINDING_CARD_BOTTOM_SLACK = 12;

/** Horizontal padding on card (`8px` left + right). */
const BINDING_CARD_PAD_X = 8;

/** Approximate average monospace char width at `fontSize: 12` (slightly conservative for wrap estimates). */
const VALUE_PREVIEW_CHAR_W = 7.1;

const VALUE_PREVIEW_CHARS_PER_LINE = Math.max(
  24,
  Math.floor((BINDING_NODE_WIDTH - 2 * BINDING_CARD_PAD_X) / VALUE_PREVIEW_CHAR_W)
);

/** Line box height for preview text (`fontSize: 12`, `wordBreak: break-all`). */
const VALUE_PREVIEW_LINE_H = 17;

/** `marginTop` + `borderTop` + `paddingTop` before preview text in BindingNode. */
const VALUE_PREVIEW_CHROME_H = 8 + 1 + 6;

/** Cap preview block height so pathological strings do not explode ELK layout. */
const MAX_VALUE_PREVIEW_BLOCK_PX = 360;

/** Cap a single kwarg row body (wrapped suffix) so one pathological line does not dominate ELK. */
const MAX_KWARG_ROW_DISPLAY_PX = 280;

function wrappedLineCount(text: string, charsPerLine: number): number {
  let lines = 0;
  for (const segment of text.split("\n")) {
    if (segment.length === 0) {
      lines += 1;
    } else {
      lines += Math.max(1, Math.ceil(segment.length / charsPerLine));
    }
  }
  return Math.max(1, lines);
}

function estimateSequenceHeight(sequence: ValueSequenceVM): number {
  const width = VALUE_PREVIEW_CHARS_PER_LINE - 5; // Space for item numbers and argument indentation.
  const lines = sequence.items.reduce((count, item) => {
    if ("text" in item) return count + wrappedLineCount(item.text, width);
    return count + wrappedLineCount(item.callee + "(…)", width) + item.kwargs.reduce(
      (args, { name, text }) => args + wrappedLineCount(`${name} = ${text}`, width), 0
    );
  }, 1 + Number(sequence.items.length < sequence.count));
  return VALUE_PREVIEW_CHROME_H + lines * VALUE_PREVIEW_LINE_H + sequence.items.length * 11;
}

/** Visible suffix on a kw row (after the name); drives wrap height in BindingNode flex rows. */
function kwargSuffixDisplayText(k: KwargVM): string {
  if (k.valueKind === "foldedRef") {
    return (k.foldedDisplayShort ?? k.foldedDisplayFull ?? "").trim();
  }
  if (
    k.valueKind === "nestedFresh" ||
    k.valueKind === "freshNested" ||
    k.valueKind === "inlineNested" ||
    k.valueKind === "collectionNested"
  ) {
    return "";
  }
  if (k.refTargetId) {
    const rp = k.refQualPath ?? k.refTargetId;
    return `→ @${rp}${k.inlinePreview ?? ""}`;
  }
  return (k.inlinePreview ?? "").trim();
}

function estimateKwargRowHeight(k: KwargVM): number {
  const text = kwargSuffixDisplayText(k);
  if (!text) {
    return BINDING_KW_ROW_H;
  }
  const nameBudget = k.name.length * VALUE_PREVIEW_CHAR_W + 10;
  const contentW = Math.max(
    64,
    BINDING_NODE_WIDTH - 2 * BINDING_CARD_PAD_X - 6 - 14 - nameBudget
  );
  const charsPerLine = Math.max(8, Math.floor(contentW / VALUE_PREVIEW_CHAR_W));
  const lines = wrappedLineCount(text, charsPerLine);
  const body = lines * VALUE_PREVIEW_LINE_H + 4;
  return Math.min(Math.max(BINDING_KW_ROW_H, body), MAX_KWARG_ROW_DISPLAY_PX);
}

function estimateProviderKwargRowsHeight(kwargs: KwargVM[]): number {
  if (kwargs.length === 0) {
    return BINDING_KW_ROW_H;
  }
  return kwargs.reduce((sum, k) => sum + estimateKwargRowHeight(k), 0);
}

function estimateFreshOverlayKwargRowsHeight(kwargs: KwargVM[]): number {
  if (kwargs.length === 0) {
    return 0;
  }
  return kwargs.reduce((sum, k) => sum + estimateKwargRowHeight(k), 0);
}

function estimateCollectionSlotRowHeight(label: string): number {
  const text = label.trim();
  if (!text) {
    return BINDING_KW_ROW_H;
  }
  const contentW = Math.max(64, BINDING_NODE_WIDTH - 2 * BINDING_CARD_PAD_X - 6 - 14);
  const charsPerLine = Math.max(8, Math.floor(contentW / VALUE_PREVIEW_CHAR_W));
  const lines = wrappedLineCount(text, charsPerLine);
  const body = lines * VALUE_PREVIEW_LINE_H + 4;
  return Math.min(Math.max(BINDING_KW_ROW_H, body), MAX_KWARG_ROW_DISPLAY_PX);
}

function estimateCollectionBlockHeight(slots: readonly { label: string }[]): number {
  let h = COLLECTION_SECTION_TOP;
  for (const s of slots) {
    h += estimateCollectionSlotRowHeight(s.label);
  }
  return h;
}

function estimateValuePreviewBlockHeight(preview: string): number {
  const text = preview.trim();
  if (!text) return 0;
  const lines = wrappedLineCount(text, VALUE_PREVIEW_CHARS_PER_LINE);
  return Math.min(VALUE_PREVIEW_CHROME_H + lines * VALUE_PREVIEW_LINE_H, MAX_VALUE_PREVIEW_BLOCK_PX);
}

const PROVIDER_CALLABLE_BLOCK_H = 20;

const PROVIDER_KW_SECTION_TOP_PAD = 8;

const COLLECTION_SECTION_TOP = 8;

const FRESH_OVERLAY_GAP = 8;

/** Extra height for ELK so estimates are not tight vs real font/wrapping. */
const ELK_ESTIMATE_HEIGHT_SLACK = 12;

const PATH_ALIAS_BLOCK_PAD = 6;
const PATH_ALIAS_ROW_EST = 20;

const DEFINITION_FILE_SUBTITLE_H = 14;

function estimateDefinitionFileSubtitleHeight(n: GraphNodeVM): number {
  if (!n.activeDefinition?.uri) return 0;
  if (n.freshInstance || n.collection) return 0;
  return DEFINITION_FILE_SUBTITLE_H;
}

/** Single qualified path row beside `out` (value / alias / external cards). */
function estimateValueLikeQualifiedPathRowHeight(n: GraphNodeVM): number {
  return n.provider || n.freshInstance || n.collection ? 0 : PATH_ALIAS_BLOCK_PAD + PATH_ALIAS_ROW_EST;
}

const BINDING_PLAIN_BODY_H = 28;

function providerBlockHeight(n: GraphNodeVM): number {
  const rowH = BINDING_KW_ROW_H;
  if (n.collection?.slots?.length) {
    return estimateCollectionBlockHeight(n.collection.slots);
  }
  if (n.provider) {
    return (
      PROVIDER_CALLABLE_BLOCK_H +
      PROVIDER_KW_SECTION_TOP_PAD +
      estimateProviderKwargRowsHeight(n.provider.kwargs)
    );
  }
  if (n.freshInstance) {
    const ov = n.freshInstance.overlayKwargs;
    return (
      PROVIDER_CALLABLE_BLOCK_H +
      PROVIDER_KW_SECTION_TOP_PAD +
      rowH +
      (ov.length > 0 ? FRESH_OVERLAY_GAP + estimateFreshOverlayKwargRowsHeight(ov) : 0)
    );
  }
  if (n.binding?.valuePreview) {
    return 0;
  }
  return BINDING_PLAIN_BODY_H;
}

export function canCollapseKwargs(n: Pick<GraphNodeVM, "provider" | "freshInstance">): boolean {
  return Boolean(n.provider || n.freshInstance);
}

/**
 * Card size for ELK and React Flow. Matches BindingNode vertical structure + padding + slack.
 */
export function estimateBindingCardSize(
  n: GraphNodeVM, kwargsCollapsed = false
): { width: number; height: number } {
  if (kwargsCollapsed && canCollapseKwargs(n)) {
    return { width: BINDING_NODE_WIDTH, height: COLLAPSED_PROVIDER_HEIGHT };
  }
  const bindingSectionH = n.binding?.valueSequence
    ? estimateSequenceHeight(n.binding.valueSequence)
    : n.binding?.valuePreview ? estimateValuePreviewBlockHeight(n.binding.valuePreview) : 0;
  const contentH =
    BINDING_HEADER_CONTENT_H +
    estimateDefinitionFileSubtitleHeight(n) +
    estimateValueLikeQualifiedPathRowHeight(n) +
    providerBlockHeight(n) +
    bindingSectionH +
    BINDING_CARD_BOTTOM_SLACK;
  const height = BINDING_CARD_PADDING_Y + contentH + ELK_ESTIMATE_HEIGHT_SLACK;
  return { width: BINDING_NODE_WIDTH, height };
}
