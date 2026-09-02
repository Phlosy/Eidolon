import { useEffect, useRef } from "react";
import type { OfficeEventBridge } from "../bridge/office-event-bridge";
import type { OfficeStateSnapshot } from "../types/office-state";

export type AuthOfficeGameCanvasProps = {
  bridge: OfficeEventBridge;
  initialState: OfficeStateSnapshot;
  label: string;
};

type DestroyableGame = { destroy: (removeCanvas: boolean, noReturn?: boolean) => void };

export function AuthOfficeGameCanvas({ bridge, initialState, label }: AuthOfficeGameCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const initialStateRef = useRef(initialState);

  useEffect(() => {
    initialStateRef.current = initialState;
    bridge.emit("office.state.replace", initialState);
  }, [bridge, initialState]);

  useEffect(() => {
    let disposed = false;
    let game: DestroyableGame | null = null;

    void import("../game/create-game").then(({ createOfficeGame }) => {
      if (!hostRef.current) return;
      const createdGame = createOfficeGame({
        parent: hostRef.current,
        bridge,
        initialState: initialStateRef.current,
      });
      if (disposed) createdGame.destroy(true);
      else game = createdGame;
    });

    return () => {
      disposed = true;
      game?.destroy(true);
      bridge.clear();
    };
  }, [bridge]);

  return (
    <div
      ref={hostRef}
      className="auth-office-game-canvas"
      data-testid="auth-office-game-canvas"
      role="img"
      aria-label={label}
    />
  );
}
