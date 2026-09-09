import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Body, H1, Lang, TableText, Telemetry, Text, TutorialText } from "./text";

describe("typography Text", () => {
  it("renders semantic levels with the shared type classes", () => {
    render(<H1>Page title</H1>);
    const h1 = screen.getByText("Page title");
    expect(h1.tagName).toBe("H1");
    expect(h1.className).toContain("type-h1");
  });

  it("applies tone classes", () => {
    render(<Body tone="muted">Body copy</Body>);
    const p = screen.getByText("Body copy");
    expect(p.className).toContain("type-body");
    expect(p.className).toContain("text-muted-foreground");
  });

  it("supports overriding the element", () => {
    render(
      <Text as="div" variant="h2">
        Section
      </Text>,
    );
    const el = screen.getByText("Section");
    expect(el.tagName).toBe("DIV");
    expect(el.className).toContain("type-h2");
  });

  it("covers table / tutorial / telemetry roles", () => {
    render(
      <>
        <TableText>cell</TableText>
        <TutorialText>hint</TutorialText>
        <Telemetry>42</Telemetry>
      </>,
    );
    expect(screen.getByText("cell").className).toContain("type-table");
    expect(screen.getByText("hint").className).toContain("type-tutorial");
    expect(screen.getByText("42").className).toContain("type-telemetry");
  });

  it("Lang tags a subtree with the active language", () => {
    render(<Lang>Mixed content</Lang>);
    expect(screen.getByText("Mixed content").getAttribute("lang")).toBe("en-US");
  });
});
