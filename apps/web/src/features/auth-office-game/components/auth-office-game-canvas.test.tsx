import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OfficeEventBridge } from "../bridge/office-event-bridge";
import { authOfficeDemoSnapshot } from "../state/office-state-adapter";
import { AuthOfficeGameCanvas } from "./auth-office-game-canvas";

const destroy = vi.fn();
const createOfficeGame = vi.fn(() => ({ destroy }));

vi.mock("../game/create-game", () => ({ createOfficeGame }));

describe("AuthOfficeGameCanvas", () => {
  it("creates one game for a mounted host and destroys it with the canvas on unmount", async () => {
    const bridge = new OfficeEventBridge();
    const { unmount } = render(
      <AuthOfficeGameCanvas
        bridge={bridge}
        initialState={authOfficeDemoSnapshot}
        label="Interactive office"
      />,
    );

    await act(async () => Promise.resolve());
    expect(screen.getByTestId("auth-office-game-canvas")).toHaveAccessibleName(
      "Interactive office",
    );
    expect(createOfficeGame).toHaveBeenCalledTimes(1);

    unmount();
    expect(destroy).toHaveBeenCalledWith(true);
  });
});
