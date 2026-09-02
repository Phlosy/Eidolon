import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EntitlementList } from "./access-tab";
import type { EmployeeEntitlement } from "../../types";

function makeEntitlement(
  id: number,
  resourceType: string,
  sources: { package_id: number; package_name: string }[],
): EmployeeEntitlement {
  return {
    entitlement: {
      id,
      key: `git:key-${id}`,
      name: `Entitlement ${id}`,
      type: "group",
      resource_type: resourceType,
      description: null,
    },
    sources,
  };
}

describe("EntitlementList", () => {
  it("groups entitlements by resource type", () => {
    render(
      <EntitlementList
        entitlements={[
          makeEntitlement(1, "git", [{ package_id: 1, package_name: "Engineering" }]),
          makeEntitlement(2, "docs", [{ package_id: 1, package_name: "Engineering" }]),
        ]}
      />,
    );
    expect(screen.getByText("git")).toBeInTheDocument();
    expect(screen.getByText("docs")).toBeInTheDocument();
    expect(screen.getAllByTestId("entitlement-row")).toHaveLength(2);
  });

  it("shows every source package on an entitlement", () => {
    render(
      <EntitlementList
        entitlements={[
          makeEntitlement(1, "git", [
            { package_id: 1, package_name: "Base Employee" },
            { package_id: 2, package_name: "Engineering" },
          ]),
        ]}
      />,
    );
    const sources = screen.getAllByTestId("entitlement-source");
    expect(sources).toHaveLength(2);
    expect(sources[0]).toHaveTextContent("Source: Base Employee");
    expect(sources[1]).toHaveTextContent("Source: Engineering");
  });
});
