import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { AuthShellLayout } from "../../features/auth/auth-shell";
import { LoginPage } from "./login-page";

const setMe = vi.fn();
const login = vi.fn();
const me = vi.fn();

vi.mock("../../features/auth/auth-context", () => ({
  useAuth: () => ({ setMe }),
}));
vi.mock("../../api", () => ({
  auth: { login: (...a: unknown[]) => login(...a), me: () => me() },
}));

function renderLoginPage() {
  return render(
    <MemoryRouter initialEntries={["/auth/login"]}>
      <Routes>
        <Route element={<AuthShellLayout />}>
          <Route path="/auth/login" element={<LoginPage />} />
          <Route path="/" element={<p>home</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("LoginPage", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    await i18n.changeLanguage("zh-CN");
  });

  it("renders the sign-in form and links to registration", () => {
    renderLoginPage();
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "注册" })).toHaveAttribute("href", "/auth/register");
    expect(screen.getByRole("heading", { level: 1, name: /EIDOLON/ })).toBeInTheDocument();
  });

  it("signs in, loads /auth/me and navigates home", async () => {
    login.mockResolvedValue({ user_id: "u", session_id: "s" });
    me.mockResolvedValue({ user_id: "u", email: "o@x.io", companies: [] });
    renderLoginPage();
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "o@x.io" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() => expect(screen.getByText("home")).toBeInTheDocument());
    expect(login).toHaveBeenCalledWith("o@x.io", "correct horse battery");
    expect(setMe).toHaveBeenCalledWith(expect.objectContaining({ user_id: "u" }));
  });

  it("shows the API's message when the password is wrong", async () => {
    login.mockRejectedValue(new Error("invalid email or password"));
    renderLoginPage();
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "o@x.io" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid email or password");
  });
});
