import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OfficeEventBridge } from "../bridge/office-event-bridge";
import { authOfficeDemoSnapshot } from "../state/office-state-adapter";
import { AuthOfficeGameCanvas } from "./auth-office-game-canvas";

const destroy = vi.fn();
const createOfficeGame = vi.fn(() => ({ destroy }));

vi.mock("../game/create-game", () => ({ createOfficeGame }));

// The dynamic import needs real task turns, not just microtasks: vitest's mocked
// module runner resolves the import through its own async hop, so a microtask-only
// flush would leave the assertion racing the effect callback.
const settle = async () => {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
};

describe("AuthOfficeGameCanvas", () => {
  beforeEach(() => {
    createOfficeGame.mockClear();
    destroy.mockClear();
  });

  it("creates one game for a mounted host and destroys it with the canvas on unmount", async () => {
    const bridge = new OfficeEventBridge();
    const unsubscribe = bridge.on("office.employee.focus", vi.fn());
    const { unmount } = render(
      <AuthOfficeGameCanvas
        bridge={bridge}
        initialState={authOfficeDemoSnapshot}
        label="Interactive office"
      />,
    );

    await settle();
    expect(screen.getByTestId("auth-office-game-canvas")).toHaveAccessibleName(
      "Interactive office",
    );
    expect(createOfficeGame).toHaveBeenCalledTimes(1);

    unmount();
    expect(destroy).toHaveBeenCalledWith(true);
    expect(bridge.listenerCount("office.employee.focus")).toBe(1);
    unsubscribe();
  });
});
