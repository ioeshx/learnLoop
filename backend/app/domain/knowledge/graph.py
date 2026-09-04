"""Deterministic knowledge graph validation."""

from collections import defaultdict, deque
from collections.abc import Iterable

from app.domain.exceptions import KnowledgeGraphError
from app.domain.knowledge.models import KnowledgeEdge, KnowledgeNode, RelationType


def validate_knowledge_graph(
    nodes: Iterable[KnowledgeNode], edges: Iterable[KnowledgeEdge]
) -> None:
    """Validate node references, goal boundaries, duplicates and prerequisite cycles."""
    node_list = list(nodes)
    edge_list = list(edges)
    node_by_id = {node.id: node for node in node_list}

    if len(node_by_id) != len(node_list):
        raise KnowledgeGraphError("knowledge node ids must be unique")

    seen_edges: set[tuple[str, str, RelationType]] = set()
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in node_by_id}

    for edge in edge_list:
        source = node_by_id.get(edge.source_node_id)
        target = node_by_id.get(edge.target_node_id)
        if source is None or target is None:
            raise KnowledgeGraphError("knowledge edge references an unknown node")
        if source.goal_id != edge.goal_id or target.goal_id != edge.goal_id:
            raise KnowledgeGraphError("knowledge edge crosses learning goal boundaries")

        signature = (edge.source_node_id, edge.target_node_id, edge.relation)
        if signature in seen_edges:
            raise KnowledgeGraphError("duplicate knowledge edge")
        seen_edges.add(signature)

        if edge.relation == RelationType.PREREQUISITE:
            adjacency[edge.source_node_id].append(edge.target_node_id)
            indegree[edge.target_node_id] += 1

    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target_id in adjacency[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                queue.append(target_id)

    if visited != len(node_by_id):
        raise KnowledgeGraphError("prerequisite relations contain a cycle")
