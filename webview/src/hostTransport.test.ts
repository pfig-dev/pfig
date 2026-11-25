import { afterEach, describe, expect, it, vi } from "vitest";

import { createGraphHostTransport } from "./hostTransport";

describe("createGraphHostTransport", () => {
  it("does not reveal source in a standalone browser", async () => {
    const fetch = vi.fn().mockResolvedValue({ json: async () => ({ ok: true, supportsSourceReveal: false }) });
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("window", {
      location: { href: "http://127.0.0.1:9000/" },
      matchMedia: () => ({ matches: false }),
    });
    const host = createGraphHostTransport();
    expect(host.canRevealSource()).toBe(false);
    host.ready();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    host.reveal("file:///tmp/main.pfig", {
      start: { line: 0, character: 0 }, end: { line: 0, character: 1 },
    });
    expect(host.canRevealSource()).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("reveals source through the VS Code host", () => {
    const postMessage = vi.fn();
    vi.stubGlobal("window", {
      acquireVsCodeApi: () => ({ postMessage, getState: () => undefined, setState: () => undefined }),
    });
    const host = createGraphHostTransport();
    const range = { start: { line: 2, character: 4 }, end: { line: 2, character: 9 } };
    expect(host.canRevealSource()).toBe(true);
    host.reveal("file:///tmp/main.pfig", range);
    expect(postMessage).toHaveBeenCalledWith({ type: "reveal", uri: "file:///tmp/main.pfig", range });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes range start to browser reveal endpoint", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce({ json: async () => ({ supportsSourceReveal: true }) })
      .mockResolvedValue({ status: 204, type: "basic" });
    const open = vi.fn();
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("window", {
      location: { href: "http://127.0.0.1:9000/" },
      matchMedia: () => ({ matches: false }),
      localStorage: { getItem: () => null, setItem: () => undefined },
      open,
    });

    const host = createGraphHostTransport();
    host.ready();
    await vi.waitFor(() => expect(host.canRevealSource()).toBe(true));
    host.reveal("file:///tmp/main.pfig", {
      start: { line: 2, character: 4 },
      end: { line: 2, character: 9 },
    });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    expect(fetch.mock.calls[1]![0]).toBe(
      "http://127.0.0.1:9000/api/reveal?uri=file%3A%2F%2F%2Ftmp%2Fmain.pfig&line=2&character=4",
    );
    expect(fetch.mock.calls[1]![1]).toEqual({ redirect: "manual" });
    expect(open).not.toHaveBeenCalled();
  });
});
