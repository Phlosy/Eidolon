import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { LoginPage } from "./login-page";

vi.mock("../../features/auth/auth-context", () => ({
  useAuth: () => ({ setAuth: vi.fn() }),
}));

vi.mock("../../features/auth/webauthn", () => ({
  passkeysAvailable: () => true,
  signInWithPasskey: vi.fn(),
}));

describe("LoginPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  it("places passkey after password login and labels the account link as registration", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    const passwordLogin = screen.getByRole("button", { name: "登录" });
    const passkeyLogin = screen.getByRole("button", { name: "使用通行密钥登录" });
    expect(
      passwordLogin.compareDocumentPosition(passkeyLogin) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "注册" })).toHaveAttribute("href", "/auth/register");
    expect(screen.getByTestId("pixel-office-scene")).toHaveAccessibleName(/像素办公室场景/);
  });

  it("lets people pause the looping office scene", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    const pause = screen.getByRole("button", { name: "暂停办公室动画" });
    expect(pause).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(pause);
    expect(pause).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("pixel-office-scene")).toHaveClass("is-paused");
  });
});
