export type GridPoint = { x: number; y: number };
export type NavigationGrid = { width: number; height: number; blocked: Set<string> };

const key = ({ x, y }: GridPoint) => `${x},${y}`;
const distance = (a: GridPoint, b: GridPoint) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);

export function findGridPath(start: GridPoint, target: GridPoint, grid: NavigationGrid): GridPoint[] {
  if (grid.blocked.has(key(target))) return [];

  const open = new Map<string, GridPoint>([[key(start), start]]);
  const cameFrom = new Map<string, GridPoint>();
  const cost = new Map<string, number>([[key(start), 0]]);

  while (open.size > 0) {
    let current = [...open.values()][0];
    for (const candidate of open.values()) {
      const candidateScore = (cost.get(key(candidate)) ?? Infinity) + distance(candidate, target);
      const currentScore = (cost.get(key(current)) ?? Infinity) + distance(current, target);
      if (candidateScore < currentScore) current = candidate;
    }

    if (key(current) === key(target)) {
      const path = [current];
      while (cameFrom.has(key(path[0]))) path.unshift(cameFrom.get(key(path[0]))!);
      return path;
    }

    open.delete(key(current));
    const neighbors = [
      { x: current.x + 1, y: current.y },
      { x: current.x - 1, y: current.y },
      { x: current.x, y: current.y + 1 },
      { x: current.x, y: current.y - 1 },
    ];

    for (const neighbor of neighbors) {
      if (
        neighbor.x < 0 ||
        neighbor.y < 0 ||
        neighbor.x >= grid.width ||
        neighbor.y >= grid.height ||
        grid.blocked.has(key(neighbor))
      ) {
        continue;
      }
      const nextCost = (cost.get(key(current)) ?? 0) + 1;
      if (nextCost < (cost.get(key(neighbor)) ?? Infinity)) {
        cost.set(key(neighbor), nextCost);
        cameFrom.set(key(neighbor), current);
        open.set(key(neighbor), neighbor);
      }
    }
  }
  return [];
}

