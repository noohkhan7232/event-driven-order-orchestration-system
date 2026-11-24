import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValue(new Error("Unit tests must mock network requests")),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
