import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { KnowledgePage } from "./knowledge-page";
import type { KnowledgeItem } from "../../types";

const { proposeMutate, reviewMutate } = vi.hoisted(() => ({
  proposeMutate: vi.fn(),
  reviewMutate: vi.fn(),
}));

const ITEMS: KnowledgeItem[] = [
  {
    id: 1,
    scope: "company",
    owner_employee_id: null,
    department_id: null,
    title: "Mission statement",
    content: "Why the company exists.",
    topic: "culture",
    status: "active",
    confidence: 0.95,
    sources: [],
    proposed_scope: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
  {
    id: 2,
    scope: "department",
    owner_employee_id: 1,
    department_id: 10,
    title: "Deploy runbook",
    content: "How to ship safely.",
    topic: "engineering",
    status: "active",
    confidence: 0.8,
    sources: [],
    proposed_scope: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
  {
    id: 3,
    scope: "department",
    owner_employee_id: 2,
    department_id: 10,
    title: "Incident checklist",
    content: "What to check first.",
    topic: "operations",
    status: "proposed",
    confidence: 0.7,
    sources: [],
    proposed_scope: "company",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
  },
];

vi.mock("../../hooks/useKnowledge", () => ({
  useKnowledgeItems: () => ({ data: ITEMS, isLoading: false, isError: false, error: null }),
  useProposeKnowledgePromotion: () => ({
    mutate: proposeMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useReviewKnowledgeProposal: () => ({
    mutate: reviewMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("../../hooks/useSystem", () => ({
  useCompany: () => ({
    data: { departments: [{ id: 10, name: "Engineering" }] },
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("../../hooks/useEmployees", () => ({
  useEmployees: () => ({
    data: [
      { id: 1, name: "Ada" },
      { id: 2, name: "Grace" },
    ],
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

describe("KnowledgePage", () => {
  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={["/knowledge"]}>
        <KnowledgePage />
      </MemoryRouter>,
    );

  it("groups items into company and department sections", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "Company knowledge" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Department knowledge" })).toBeInTheDocument();
    expect(screen.getByText("Mission statement")).toBeInTheDocument();
    expect(screen.getByText("Deploy runbook")).toBeInTheDocument();
    expect(screen.getByText("Incident checklist")).toBeInTheDocument();
    // 归属信息：部门名与贡献者姓名解析自 company/employee 查询。
    expect(screen.getAllByText(/Engineering/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Ada/)).toBeInTheDocument();
  });

  it("propose flow: opens the dialog and submits the chosen target scope", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Propose promotion" }));
    expect(screen.getByRole("dialog", { name: "Promote knowledge item" })).toBeInTheDocument();
    // department 项的唯一可选目标是 company。
    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("company");

    fireEvent.click(screen.getByRole("button", { name: "Submit proposal" }));
    expect(proposeMutate).toHaveBeenCalledWith(
      { itemId: 2, targetScope: "company" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("proposed items render approve/reject which call the review mutation", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(reviewMutate).toHaveBeenCalledWith({ itemId: 3, approve: true });

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(reviewMutate).toHaveBeenCalledWith({ itemId: 3, approve: false });
  });
});
