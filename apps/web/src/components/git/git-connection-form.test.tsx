import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { GitConnectionForm } from "./git-connection-form";

function renderForm() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <GitConnectionForm open={true} onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

describe("GitConnectionForm", () => {
  it("requires a name — submit stays disabled without one", () => {
    renderForm();
    const submit = screen.getByRole("button", { name: "Add Connection" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("https://gitlab.example.com"), {
      target: { value: "https://gitlab.example.com" },
    });
    expect(submit).toBeDisabled();
  });

  it("requires a base URL — submit stays disabled without one", () => {
    renderForm();
    const submit = screen.getByRole("button", { name: "Add Connection" });
    fireEvent.change(screen.getByPlaceholderText("Company GitLab"), {
      target: { value: "Company GitLab" },
    });
    expect(submit).toBeDisabled();
  });

  it("enables submit once name and base URL are filled", () => {
    renderForm();
    fireEvent.change(screen.getByPlaceholderText("Company GitLab"), {
      target: { value: "Company GitLab" },
    });
    fireEvent.change(screen.getByPlaceholderText("https://gitlab.example.com"), {
      target: { value: "https://gitlab.example.com" },
    });
    expect(screen.getByRole("button", { name: "Add Connection" })).toBeEnabled();
  });

  it("switches the base URL placeholder with the selected platform", () => {
    renderForm();
    fireEvent.change(screen.getByDisplayValue("GitLab"), { target: { value: "github" } });
    expect(screen.getByPlaceholderText("https://github.example.com")).toBeInTheDocument();
  });

  it("renders the token field as a write-only password input", () => {
    renderForm();
    // The Dialog portals to document.body, so query there rather than the render container.
    const tokenInput = document.querySelector('input[type="password"]');
    expect(tokenInput).toBeInTheDocument();
  });
});
