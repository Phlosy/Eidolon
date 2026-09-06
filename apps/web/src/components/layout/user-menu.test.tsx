import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthState } from "../../types";

/**
 * 账号菜单的行为测试：卡片开合、三个账号动作的去向。
 * API 与 AuthContext 都 mock 掉 —— 这里要钉住的是"点了什么、去了哪页"。
 */

const state = vi.hoisted(() => ({
  auth: null as AuthState | null,
  setAuth: vi.fn(),
  logout: vi.fn(),
  requestAccountDeletion: vi.fn(),
}));

vi.mock("../../api/auth", () => ({
  logout: state.logout,
  requestAccountDeletion: state.requestAccountDeletion,
}));

vi.mock("../../features/auth/auth-context", () => ({
  useAuth: () => ({ auth: state.auth, setAuth: state.setAuth }),
}));

import { UserMenu } from "./user-menu";

function renderMenu() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<UserMenu />} />
        <Route path="/auth/login" element={<div>login page</div>} />
        <Route path="/auth/register" element={<div>register page</div>} />
        <Route path="/settings/profile" element={<div>profile page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  state.auth = {
    user: { id: 1, email: "ada@example.com", display_name: "Ada", avatar: "" },
    membership: { role: "OWNER" },
    company: {},
  } as unknown as AuthState;
  state.setAuth.mockClear();
  state.logout.mockReset().mockResolvedValue(undefined);
  state.requestAccountDeletion
    .mockReset()
    .mockResolvedValue({ email: "ada@example.com", development_verification_token: null });
});

describe("账号菜单", () => {
  it("点击头像展开卡片：身份摘要 + 设置入口 + 三个账号动作", () => {
    renderMenu();
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByText("Ada")).toBeTruthy();
    expect(screen.getByText("ada@example.com")).toBeTruthy();
    expect(screen.getByText("OWNER")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Profile" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Switch account" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Delete account" })).toBeTruthy();
  });

  it("个人资料入口进入设置的个人设置分区", () => {
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Profile" }));
    expect(screen.getByText("profile page")).toBeTruthy();
  });

  it("退出登录：结束会话、清空本地状态、回到登录页", async () => {
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    await waitFor(() => expect(state.logout).toHaveBeenCalledTimes(1));
    expect(state.setAuth).toHaveBeenCalledWith(null);
    await waitFor(() => expect(screen.getByText("login page")).toBeTruthy());
    expect(state.requestAccountDeletion).not.toHaveBeenCalled();
  });

  it("注销账号：确认后只发确认邮件，账号在点邮件链接前不会被删", async () => {
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete account" }));

    // 菜单已收起，确认对话框里才会发起注销请求
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(state.requestAccountDeletion).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Send confirmation email" }));
    await waitFor(() => expect(state.requestAccountDeletion).toHaveBeenCalledTimes(1));

    // 请求只发邮件：不退出、不跳转，对话框提示去收邮件
    expect(state.setAuth).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByText(/Confirmation email sent to ada@example.com/)).toBeTruthy(),
    );
    expect(screen.queryByText("register page")).toBeNull();
    expect(state.logout).not.toHaveBeenCalled();
  });
});
