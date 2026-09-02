# Authentication Office Game Architecture

## Boundary

`src/features/auth-office-game` is a presentation feature used only by `AuthShell`. It must not
import authenticated employee queries, mutate company data, or replace the application's
authenticated `/office` page.

```text
AuthShell (React)
├── semantic HUD, pause and employee focus controls
├── OfficeStateAdapter (demo before authentication; live-capable contract)
└── OfficeEventBridge
    └── AuthOfficeGameCanvas
        └── Phaser.Game
            ├── BootScene
            ├── PreloadScene
            └── OfficeScene
                ├── Tiled map and texture atlas
                ├── EmployeeSprite entities
                ├── A* NavigationSystem
                ├── OfficeAssignmentService / MeetingSystem
                └── EmployeeBehaviorSystem
```

Phaser never reads a React store or polls the backend. React publishes normalized snapshots or
discrete events. Phaser publishes selection/focus events; React owns any business panel or route.

## Map contract

The default Tiled-compatible JSON uses 32×32 orthogonal tiles and these ordered layers:

1. `Floor`
2. `Walls`
3. `Wall Decoration`
4. `Furniture Back`
5. `Furniture`
6. `Interaction`
7. `Zones`
8. `Characters`
9. `Furniture Front`
10. `Foreground`
11. `Lighting Effects`

Zones and interaction points are data. Employee names never select positions. Assignment uses
role and department, then falls back to the shared workspace.

## State and behavior

`OfficeStateSnapshot` is versioned and may have `demo` or `live` provenance. The authentication
cover uses the deterministic demo snapshot because protected company state is not available
before login.

| Employee status | Behavior state | Destination |
| --- | --- | --- |
| `WORKING` | `WORKING` | Assigned workstation |
| `MEETING` | `MEETING` | Claimed meeting seat |
| `LEARNING`, `RESEARCHING` | `LEARNING` | Bookshelf / study point |
| `REFLECTING` | `INTERACTING` | Whiteboard |
| `IDLE` | `IDLE` | Coffee / lounge point, low frequency |
| `OFFLINE` | `OFFLINE` | Entrance, then hidden/removed by lifecycle event |
| `ERROR` | `ERROR` | Current tile with subtle warning treatment |

The behavior system records the current state, so task text updates do not restart movement or
animation every frame. A status transition issues a new directive and route.

## Lifecycle and extension seam

- The React host dynamically imports the game module.
- Phaser registry receives the bridge and initial snapshot in `preBoot`, before scenes start.
- Scene shutdown unsubscribes bridge listeners.
- React unmount calls `game.destroy(true)` and clears the bridge.
- A future authenticated consumer can send `office.state.replace` and lifecycle events through
  the same bridge without changing entity or scene contracts.
