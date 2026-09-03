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

vi.mock("../../features/auth-office-game/components/auth-office-game-canvas", () => ({
  AuthOfficeGameCanvas: ({ label }: { label: string }) => (
    <div role="img" aria-label={label} data-testid="auth-office-game-canvas" />
  ),
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
    expect(screen.getByRole("heading", { level: 1, name: "EIDOLON" })).toBeInTheDocument();
    expect(screen.getByTestId("auth-shell")).toHaveClass("auth-world-shell");
    expect(screen.getByTestId("auth-office-game")).toHaveAccessibleName(/2D 像素办公室/);
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
    expect(screen.getByTestId("auth-office-game")).toHaveClass("is-paused");
  });

  it("introduces each employee and pauses that character while they speak", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    const engineer = screen.getByRole("button", { name: /林舟，工程负责人/ });
    expect(engineer).toHaveAttribute("aria-expanded", "false");

    fireEvent.pointerEnter(engineer);
    expect(engineer).toHaveAttribute("aria-expanded", "true");
    expect(engineer).toHaveClass("is-speaking");
    expect(screen.getByText("构建通过了，准备今天的发布。")).toBeInTheDocument();

    fireEvent.pointerLeave(engineer);
    expect(engineer).toHaveAttribute("aria-expanded", "false");

    const operations = screen.getByRole("button", { name: /苏禾，运营负责人/ });
    fireEvent.pointerEnter(operations);
    expect(screen.getByText("客户反馈已整理，下午同步给团队。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /陈默，项目负责人/ })).toBeInTheDocument();
  });

  it("reveals the office introduction from an in-world notice", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    const notice = screen.getByRole("button", { name: "打开办公室导览" });
    expect(notice).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog", { name: "办公室导览" })).not.toBeInTheDocument();

    fireEvent.click(notice);
    expect(notice).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog", { name: "办公室导览" })).toHaveTextContent(
      "你的 AI 公司，从这里开始运转。",
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭办公室导览" }));
    expect(screen.queryByRole("dialog", { name: "办公室导览" })).not.toBeInTheDocument();
  });
});
