import {
  useEffect,
  useRef,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import { toPng } from "html-to-image";
import {
  getNodesBounds,
  getViewportForBounds,
  useReactFlow,
  type Node,
} from "reactflow";

import {
  BINDING_NODE_WIDTH,
  EXPORT_LEGEND_FLOW_HEIGHT,
  EXPORT_LEGEND_NODE_ID,
  OVERVIEW_LEGEND_FLOW_HEIGHT,
} from "./graphBindingConstants";
import type { GraphViewModel } from "./types";

export type GraphExportColorMode = "dark" | "light";

export type ExportPngOpts = {
  exportDpi: number;
  exportMaxEdgePx: number;
  colorMode: GraphExportColorMode;
};

type VscodeApi = { postMessage: (msg: unknown) => void };

const CSS_PX_PER_INCH = 96;

const ALPHA_CROP_THRESHOLD = 8;
const CROP_PADDING_PX = 2;

/**
 * Trim transparent margins from the PNG (removes letterboxing from viewport vs graph aspect ratio).
 */
async function cropPngDataUrlToOpaqueBounds(dataUrl: string): Promise<string> {
  const img = new Image();
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve();
    img.onerror = () => reject(new Error("Failed to decode export image"));
    img.src = dataUrl;
  });

  const w = img.naturalWidth;
  const h = img.naturalHeight;
  if (w < 1 || h < 1) {
    return dataUrl;
  }

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return dataUrl;
  }
  ctx.drawImage(img, 0, 0);

  let imageData: ImageData;
  try {
    imageData = ctx.getImageData(0, 0, w, h);
  } catch {
    return dataUrl;
  }

  const d = imageData.data;
  let minX = w;
  let minY = h;
  let maxX = -1;
  let maxY = -1;

  for (let y = 0; y < h; y++) {
    const row = y * w * 4;
    for (let x = 0; x < w; x++) {
      if (d[row + x * 4 + 3] > ALPHA_CROP_THRESHOLD) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }

  if (maxX < minX || maxY < minY) {
    return dataUrl;
  }

  minX = Math.max(0, minX - CROP_PADDING_PX);
  minY = Math.max(0, minY - CROP_PADDING_PX);
  maxX = Math.min(w - 1, maxX + CROP_PADDING_PX);
  maxY = Math.min(h - 1, maxY + CROP_PADDING_PX);

  const cw = maxX - minX + 1;
  const ch = maxY - minY + 1;
  const out = document.createElement("canvas");
  out.width = cw;
  out.height = ch;
  const octx = out.getContext("2d");
  if (!octx) {
    return dataUrl;
  }
  octx.drawImage(canvas, minX, minY, cw, ch, 0, 0, cw, ch);
  return out.toDataURL("image/png");
}

const raf2 = (): Promise<void> =>
  new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });

type Props = {
  runExportRef: MutableRefObject<((opts: ExportPngOpts) => Promise<void>) | null>;
  nodesRef: MutableRefObject<Node[]>;
  setNodes: Dispatch<SetStateAction<Node[]>>;
  vmRef: MutableRefObject<GraphViewModel | null>;
  vscode: VscodeApi;
  clearHighlights: () => void;
  setExportingPng: (v: boolean) => void;
  setCaptureColorMode: (m: GraphExportColorMode | null) => void;
  exportLockRef: MutableRefObject<boolean>;
  overview: boolean;
};

/**
 * Registers the PNG export implementation (needs useReactFlow — must render under ReactFlow).
 */
export function PngExportController({
  runExportRef,
  nodesRef,
  setNodes,
  vmRef,
  vscode,
  clearHighlights,
  setExportingPng,
  setCaptureColorMode,
  exportLockRef,
  overview,
}: Props) {
  const { setViewport, getViewport, screenToFlowPosition } = useReactFlow();
  const busyRef = useRef(false);

  useEffect(() => {
    runExportRef.current = async ({
      exportDpi,
      exportMaxEdgePx,
      colorMode,
    }: ExportPngOpts) => {
      if (busyRef.current) {
        return;
      }
      busyRef.current = true;
      exportLockRef.current = true;

      const postErr = (message: string) => {
        vscode.postMessage({ type: "exportPngError", message });
      };

      let exportViewportApplied = false;
      let prevViewport = { x: 0, y: 0, zoom: 1 };

      try {
        const vm = vmRef.current;
        const layoutNodes = nodesRef.current.filter((n) => n.id !== EXPORT_LEGEND_NODE_ID && !n.parentNode);

        if (!vm?.ok) {
          postErr("Graph has errors; fix them or refresh before exporting.");
          return;
        }
        if (layoutNodes.length === 0) {
          postErr("Graph is empty.");
          return;
        }

        const bounds = getNodesBounds(layoutNodes);
        if (
          !Number.isFinite(bounds.width) ||
          !Number.isFinite(bounds.height) ||
          bounds.width < 4 ||
          bounds.height < 4
        ) {
          postErr("Graph bounds are invalid; try refreshing the layout.");
          return;
        }

        clearHighlights();
        setCaptureColorMode(colorMode);
        setExportingPng(true);
        await raf2();

        const viewportEl = document.querySelector(".react-flow__viewport") as HTMLElement | null;
        if (!viewportEl || viewportEl.clientWidth < 8 || viewportEl.clientHeight < 8) {
          postErr("Graph viewport is too small. Resize the graph panel and try again.");
          return;
        }

        const captureEl =
          (viewportEl.closest(".react-flow") as HTMLElement | null) ?? viewportEl;

        const vw = viewportEl.clientWidth;
        const vh = viewportEl.clientHeight;
        prevViewport = getViewport();

        const vp = getViewportForBounds(bounds, vw, vh, 0.02, 16, 0.02);
        setViewport(vp, { duration: 0 });
        exportViewportApplied = true;
        await raf2();

        const pad = 12;
        const vr = viewportEl.getBoundingClientRect();
        const br = screenToFlowPosition({
          x: vr.right - pad,
          y: vr.bottom - pad,
        });
        const legendX = br.x - BINDING_NODE_WIDTH;
        const legendY = br.y - (overview ? OVERVIEW_LEGEND_FLOW_HEIGHT : EXPORT_LEGEND_FLOW_HEIGHT);

        setNodes((prev) => {
          const base = prev.filter((n) => n.id !== EXPORT_LEGEND_NODE_ID);
          return [
            ...base,
            {
              id: EXPORT_LEGEND_NODE_ID,
              type: "legend",
              position: { x: legendX, y: legendY },
              zIndex: 2000,
              draggable: false,
              selectable: false,
              focusable: false,
              data: { overview },
            },
          ];
        });
        await raf2();

        const longView = Math.max(vw, vh);
        const dpiRatio = exportDpi / CSS_PX_PER_INCH;
        const pixelRatio = Math.max(
          1,
          Math.min(dpiRatio, exportMaxEdgePx / longView)
        );

        const dataUrl = await toPng(captureEl, {
          pixelRatio,
          cacheBust: true,
        });

        const cropped = await cropPngDataUrlToOpaqueBounds(dataUrl);

        const comma = cropped.indexOf(",");
        const base64 = comma >= 0 ? cropped.slice(comma + 1) : cropped;
        vscode.postMessage({ type: "exportPngResult", base64 });
      } catch (e) {
        vscode.postMessage({
          type: "exportPngError",
          message: e instanceof Error ? e.message : String(e),
        });
      } finally {
        setNodes((prev) => prev.filter((n) => n.id !== EXPORT_LEGEND_NODE_ID));
        if (exportViewportApplied) {
          setViewport(prevViewport, { duration: 0 });
        }
        setCaptureColorMode(null);
        setExportingPng(false);
        busyRef.current = false;
        exportLockRef.current = false;
      }
    };

    return () => {
      runExportRef.current = null;
    };
  }, [
    clearHighlights,
    exportLockRef,
    getViewport,
    nodesRef,
    overview,
    runExportRef,
    screenToFlowPosition,
    setNodes,
    setCaptureColorMode,
    setExportingPng,
    setViewport,
    vmRef,
    vscode,
  ]);

  return null;
}
