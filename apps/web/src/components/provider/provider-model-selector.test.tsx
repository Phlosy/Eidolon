import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProviderModelSelector } from "./provider-model-selector";

const HOOK = vi.hoisted(() => ({ modelCalls: [] as unknown[][] }));

vi.mock("../../hooks/useProviders", () => ({
  useProviderModels: (...args: unknown[]) => {
    HOOK.modelCalls.push(args);
    return {
      data: { models: ["deepseek-chat"], source: "api" },
      isFetching: false,
      isError: false,
    };
  },
}));

/** 员工级账号探测不到模型的根因在"作用域没传下去"，这里钉住组件透传。 */
describe("ProviderModelSelector", () => {
  it("把 employeeId 透传给模型探测（否则员工级账号 404）", () => {
    render(<ProviderModelSelector providerId={2} value="" onChange={() => {}} employeeId={7} />);
    expect(HOOK.modelCalls.at(-1)).toEqual([2, true, 7]);
  });

  it("公司级账号不传作用域", () => {
    render(<ProviderModelSelector providerId={2} value="" onChange={() => {}} />);
    expect(HOOK.modelCalls.at(-1)).toEqual([2, true, undefined]);
  });
});
