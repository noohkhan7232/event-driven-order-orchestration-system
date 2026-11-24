import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect } from "vitest";
import { ToastViewport, toast } from "@/components/toasts";
import { strings } from "@/lib/strings";
it("announces and dismisses a toast without injecting styles or HTML", async () => {
  const styles = document.querySelectorAll("style").length;
  render(<ToastViewport />);
  act(() => {
    toast.error("<b>safe message</b>");
  });
  expect(screen.getByRole("alert")).toHaveTextContent("<b>safe message</b>");
  expect(document.querySelector("b")).toBeNull();
  expect(document.querySelectorAll("style")).toHaveLength(styles);
  await userEvent.click(screen.getByRole("button", { name: strings.dismiss }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
