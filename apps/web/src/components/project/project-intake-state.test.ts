import { describe, expect, it } from "vitest";
import {
  createEmptyIntake,
  normalizeRequirements,
  validateIntakeStep,
} from "./project-intake-state";

describe("project intake state", () => {
  it("keeps all mandatory human review gates enabled", () => {
    const intake = createEmptyIntake();
    expect(intake.review_configuration).toMatchObject({
      requirements_review: true,
      design_review: true,
      acceptance_review: true,
    });
  });

  it("assigns stable requirement codes without overwriting explicit codes", () => {
    const normalized = normalizeRequirements([
      { title: "Board", description: "", priority: "must", acceptance_criteria: "Visible" },
      {
        code: "REQ-900",
        title: "Input",
        description: "",
        priority: "must",
        acceptance_criteria: "Works",
      },
    ]);
    expect(normalized.map((item) => item.code)).toEqual(["REQ-001", "REQ-900"]);
  });

  it("returns field-addressable errors for incomplete steps", () => {
    const intake = createEmptyIntake();
    expect(validateIntakeStep("basic", intake)).toEqual(
      expect.objectContaining({ name: expect.any(String), code: expect.any(String) }),
    );
    expect(validateIntakeStep("requirements", intake)).toEqual(
      expect.objectContaining({ requirements: expect.any(String) }),
    );
  });
});
