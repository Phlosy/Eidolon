import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import { eid } from "../../utils/format";
import { toFlowGraph, type FlowNodeData } from "../../utils/graph";
import { TASK_STATUS_NODE_CLASS } from "../../utils/status";
import type { ProjectGraph } from "../../types";
import { EmptyState } from "../common/states";

function TaskNode({ id, data }: NodeProps<Node<FlowNodeData>>) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "w-52 rounded-md border px-3 py-2 shadow-sm",
        TASK_STATUS_NODE_CLASS[data.status],
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground" />
      <p className="truncate text-xs font-medium">{data.label}</p>
      <p className="mt-0.5 flex items-center justify-between font-mono text-[10px] text-muted-foreground">
        <span>{eid(id)}</span>
        <span>{enumLabel(t, "project:taskStatus", data.status)}</span>
      </p>
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground" />
    </div>
  );
}

const nodeTypes = { task: TaskNode };

export function ProjectGraphView({ graph }: { graph: ProjectGraph }) {
  const { t } = useTranslation();
  const { nodes, edges } = useMemo(() => toFlowGraph(graph), [graph]);

  if (nodes.length === 0) {
    return <EmptyState title={t("project:graphEmptyTitle")} hint={t("project:graphEmptyHint")} />;
  }

  return (
    <div className="h-80 overflow-hidden rounded-lg border border-border bg-background">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
