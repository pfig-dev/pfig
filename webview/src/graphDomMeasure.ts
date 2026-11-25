/**
 * Read actual rendered heights of graph cards for ELK (estimates often drift on wrapped text).
 */

function cardHeightFromWrapper(wrap: Element): number {
  const card = wrap.querySelector(".binding-node");
  const el = (card ?? wrap) as HTMLElement;
  const h = el.offsetHeight;
  return h > 0 ? Math.round(h) : 0;
}

/** Attribute on a wrapper around each card in the off-screen measure layer in GraphApp. */
export const PFIG_MEASURE_ID_ATTR = "data-pfig-measure-id";

/**
 * Heights from the hidden measure layer: direct `[data-pfig-measure-id]` wrappers around `BindingNode`.
 */
export function measureBindingCardHeightsFromMeasureLayer(
  container: Element | null
): Map<string, number> {
  const out = new Map<string, number>();
  if (!container) {
    return out;
  }
  for (const wrap of container.querySelectorAll(`[${PFIG_MEASURE_ID_ATTR}]`)) {
    const id = wrap.getAttribute(PFIG_MEASURE_ID_ATTR);
    if (!id) {
      continue;
    }
    const h = cardHeightFromWrapper(wrap);
    if (h > 0) {
      out.set(id, h);
    }
  }
  return out;
}

export function waitNextPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => resolve());
    });
  });
}
