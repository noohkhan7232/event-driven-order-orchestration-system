import { expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { SecretOnce, useSecretDisclosure } from "@/components/operations-ui";
import { strings as s } from "@/lib/strings";
vi.mock("next/navigation", () => ({ usePathname: () => "/api-keys" }));
it("requires a saved acknowledgement before dismissal and clears on departure", () => {
  const forget = vi.fn();
  const view = render(
    <SecretOnce value="SYNTHETIC_SECRET_NOT_VALID" onForget={forget} />,
  );
  expect(screen.getByRole("button", { name: s.ops.done })).toBeDisabled();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(forget).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText(s.ops.saved));
  fireEvent.click(screen.getByRole("button", { name: s.ops.done }));
  expect(forget).toHaveBeenCalled();
  view.unmount();
});
it("forgets a revealed secret when hidden and rejects late responses while inactive", () => {
  const visibility = vi
    .spyOn(document, "visibilityState", "get")
    .mockReturnValue("visible");
  const { result, unmount } = renderHook(() => useSecretDisclosure());
  act(() => result.current.publish("SYNTHETIC_SECRET_NOT_VALID"));
  expect(result.current.value).toBeTruthy();
  visibility.mockReturnValue("hidden");
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  expect(result.current.value).toBeNull();
  act(() => result.current.publish("LATE_SYNTHETIC_SECRET"));
  expect(result.current.value).toBeNull();
  const publish = result.current.publish;
  unmount();
  expect(() => publish("AFTER_UNMOUNT")).not.toThrow();
  visibility.mockRestore();
});

it("does not reveal an earlier request's secret after leaving and returning", () => {
  const visibility = vi
    .spyOn(document, "visibilityState", "get")
    .mockReturnValue("visible");
  const { result } = renderHook(() => useSecretDisclosure());
  const reveal = result.current.begin();
  visibility.mockReturnValue("hidden");
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  visibility.mockReturnValue("visible");
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  act(() => reveal("LATE_SYNTHETIC_SECRET"));
  expect(result.current.value).toBeNull();
  visibility.mockRestore();
});

it("copies only the requested transient value without writing browser storage", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const store = vi.spyOn(Storage.prototype, "setItem");
  render(<SecretOnce value="SYNTHETIC_COPY_VALUE" onForget={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: s.ops.copy }));
  expect(writeText).toHaveBeenCalledWith("SYNTHETIC_COPY_VALUE");
  expect(store).not.toHaveBeenCalled();
  store.mockRestore();
});
