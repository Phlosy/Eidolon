import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "./dialog";

function renderDialog() {
  return render(
    <Dialog open onOpenChange={() => {}} title="Provider" description="configure">
      <p>step 1</p>
      <p>step 2</p>
    </Dialog>,
  );
}

describe("Dialog", () => {
  it("renders title, description and children", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "Provider" })).toBeInTheDocument();
    expect(screen.getByText("configure")).toBeInTheDocument();
    expect(screen.getByText("step 2")).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    const onChange = vi.fn();
    render(
      <Dialog open onOpenChange={onChange} title="T">
        <p>body</p>
      </Dialog>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("keeps the panel within the viewport and scrolls overflowing content", () => {
    renderDialog();
    const panel = screen.getByTestId("dialog-panel");
    // 小屏/内容过长（如自动检测出很多模型）时：面板不超出视口，内容区内滚
    expect(panel.className).toContain("max-h-[calc(100vh-2rem)]");
    expect(panel.className).toContain("flex-col");
    const body = screen.getByTestId("dialog-body");
    expect(body.className).toContain("overflow-y-auto");
  });
});
