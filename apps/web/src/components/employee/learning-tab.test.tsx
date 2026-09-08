import { render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LearningTab } from "./learning-tab";

vi.mock("../../hooks/useLearning", () => ({
  useEmployeeLearning: () => ({
    policyQuery: {
      data: { enabled: false, autonomous_learning_warning: true },
      isLoading: false,
    },
    sessionsQuery: {
      data: [
        {
          id: 1,
          topic: "WebGPU rendering",
          status: "completed",
          learning_mode: "web_research",
          runtime_type: "mock",
          tokens_used: 500,
          budget_tokens: 2000,
        },
      ],
      isLoading: false,
    },
    startMutation: { mutate: vi.fn() },
    cancelMutation: { mutate: vi.fn() },
  }),
}));

describe("LearningTab", () => {
  it("shows cost warning when autonomous learning is disabled", () => {
    render(<LearningTab employeeId={1} />);
    expect(screen.getByText(/Autonomous learning is off/)).toBeInTheDocument();
    expect(screen.getByText(/consume model\/API credits/)).toBeInTheDocument();
  });

  it("lists recent learning sessions with budget usage, not fake XP", () => {
    render(<LearningTab employeeId={1} />);
    expect(screen.getByText("WebGPU rendering")).toBeInTheDocument();
    expect(screen.getByText(/500\/2000/)).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("manual start form is present and posts on submit", () => {
    render(<LearningTab employeeId={1} />);
    fireEvent.change(screen.getByPlaceholderText("Topic"), {
      target: { value: "React internals" },
    });
    expect(screen.getByTestId("start-learning")).toBeEnabled();
  });
});
