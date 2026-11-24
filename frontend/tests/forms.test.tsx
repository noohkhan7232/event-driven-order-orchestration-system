import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi, beforeEach } from "vitest";
import { AuthForm } from "@/components/auth-form";
import { strings as s } from "@/lib/strings";
const { replace, refresh, request } = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
  request: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, refresh }) }));
vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  api: request,
}));
beforeEach(() => {
  request.mockReset();
  replace.mockReset();
  refresh.mockReset();
});
it("validates credentials, logs in and replaces history without storing JWT", async () => {
  const user = userEvent.setup();
  request.mockResolvedValue({ ok: true });
  render(<AuthForm mode="login" />);
  expect(
    screen.getByRole("button", { name: s.login }).closest("form"),
  ).toHaveAttribute("method", "post");
  expect(
    screen.getByRole("button", { name: s.login }).closest("form"),
  ).toHaveAttribute("action", "/bff/auth/login");
  await user.click(screen.getByRole("button", { name: s.login }));
  expect(request).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText(s.email), "owner@example.com");
  await user.type(screen.getByLabelText(s.password), "password123");
  await user.click(screen.getByRole("button", { name: s.login }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
  expect(request.mock.calls[0][0]).toBe("/bff/auth/login");
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});
it("submits registration and invite code using the backend contract", async () => {
  const user = userEvent.setup();
  request.mockResolvedValue({ id: "tenant" });
  render(<AuthForm mode="register" />);
  expect(
    screen.getByRole("button", { name: s.submitRegister }).closest("form"),
  ).toHaveAttribute("method", "post");
  expect(
    screen.getByRole("button", { name: s.submitRegister }).closest("form"),
  ).toHaveAttribute("action", "/bff/auth/register");
  for (const [label, value] of [
    [s.business, "Test store"],
    [s.owner, "Owner"],
    [s.email, "owner@example.com"],
    [s.password, "password123"],
    [s.invite, "invite-fixture"],
  ])
    await user.type(screen.getByLabelText(label), value);
  await user.click(screen.getByRole("button", { name: s.submitRegister }));
  await waitFor(() =>
    expect(replace).toHaveBeenCalledWith("/login?registered=1"),
  );
  expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({
    business_name: "Test store",
    owner_name: "Owner",
    owner_email: "owner@example.com",
    owner_password: "password123",
    invite_code: "invite-fixture",
  });
});
it("shows registration-closed and a visible retry countdown that disables resubmit", async () => {
  const { ApiError } = await import("@/lib/api/client");
  const user = userEvent.setup();
  request.mockRejectedValue(new ApiError(429, "rate_limited", 1));
  render(<AuthForm mode="login" />);
  await user.type(screen.getByLabelText(s.email), "owner@example.com");
  await user.type(screen.getByLabelText(s.password), "password123");
  await user.click(screen.getByRole("button", { name: s.login }));
  expect(await screen.findByText(s.retryAfter(1))).toBeInTheDocument();
  expect(screen.getByRole("button", { name: s.login })).toBeDisabled();
  await waitFor(
    () => expect(screen.getByRole("button", { name: s.login })).toBeEnabled(),
    { timeout: 2000 },
  );
  request.mockRejectedValue(new ApiError(403, "registration_closed"));
  await user.click(screen.getByRole("button", { name: s.login }));
  expect(
    await screen.findByText(s.errors.registration_closed),
  ).toBeInTheDocument();
});
