"""Dependency graph management and analysis for code components."""
from typing import Dict, List, Set, Optional, Iterator, Any
import networkx as nx
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class Node:
    """Represents a node in the dependency graph."""
    id: str
    component_type: str
    language: str
    file_path: str
    metadata: Dict[str, Any]

class DependencyGraph:
    """Manages dependencies between code components using a directed graph."""
    
    def __init__(self):
        self.graph = nx.DiGraph()
        self._cycle_cache: Dict[str, List[List[str]]] = {}
    
    def add_node(self, node: Node) -> None:
        """Add a node to the graph with its attributes."""
        self.graph.add_node(
            node.id,
            component_type=node.component_type,
            language=node.language,
            file_path=node.file_path,
            metadata=node.metadata
        )
    
    def add_edge(self, from_id: str, to_id: str, edge_type: str = 'depends_on') -> None:
        """Add a dependency edge between nodes."""
        if from_id not in self.graph or to_id not in self.graph:
            logger.warning(f"Attempted to add edge between nonexistent nodes: {from_id} -> {to_id}")
            return
        
        self.graph.add_edge(from_id, to_id, edge_type=edge_type)
        self._cycle_cache.clear()  # Invalidate cycle cache
    
    def get_dependencies(self, node_id: str) -> Set[str]:
        """Get all direct dependencies of a node."""
        if node_id not in self.graph:
            return set()
        return set(self.graph.successors(node_id))
    
    def get_dependents(self, node_id: str) -> Set[str]:
        """Get all nodes that directly depend on the given node."""
        if node_id not in self.graph:
            return set()
        return set(self.graph.predecessors(node_id))
    
    def get_all_dependencies(self, node_id: str) -> Set[str]:
        """Get all dependencies (direct and indirect) of a node."""
        if node_id not in self.graph:
            return set()
        return set(nx.descendants(self.graph, node_id))
    
    def get_dependency_levels(self) -> List[List[str]]:
        """Get nodes grouped by their dependency levels.
        
        Returns:
            List of lists where each inner list contains nodes at the same dependency level.
        """
        if not self.graph:
            return []
        
        # Reverse the graph to get correct dependency order
        reversed_graph = self.graph.reverse()
        
        # Get topological generations (levels)
        try:
            levels = list(nx.topological_generations(reversed_graph))
            return [list(level) for level in levels]
        except nx.NetworkXUnfeasible:
            logger.error("Cyclic dependencies detected, falling back to approximate levels")
            return self._get_approximate_levels()
    
    def _get_approximate_levels(self) -> List[List[str]]:
        """Get approximate dependency levels when cycles exist."""
        # Use Kahn's algorithm with cycle breaking
        in_degree = dict(self.graph.in_degree())
        levels: List[List[str]] = []
        current_level = set()
        
        # Find nodes with no dependencies
        for node in self.graph.nodes():
            if in_degree[node] == 0:
                current_level.add(node)
        
        while current_level:
            levels.append(list(current_level))
            next_level = set()
            
            for node in current_level:
                for successor in self.graph.successors(node):
                    in_degree[successor] -= 1
                    if in_degree[successor] == 0:
                        next_level.add(successor)
            
            current_level = next_level
        
        # Handle remaining nodes (in cycles)
        remaining = set(n for n, d in in_degree.items() if d > 0)
        if remaining:
            levels.append(list(remaining))
        
        return levels
    
    def detect_cycles(self) -> List[List[str]]:
        """Detect and return all cycles in the dependency graph."""
        if not self._cycle_cache:
            try:
                cycles = list(nx.simple_cycles(self.graph))
                self._cycle_cache['cycles'] = cycles
            except Exception as e:
                logger.error(f"Error detecting cycles: {str(e)}")
                return []
        
        return self._cycle_cache.get('cycles', [])
    
    def get_node_info(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get all information about a node."""
        if node_id not in self.graph:
            return None
        
        return {
            'id': node_id,
            **self.graph.nodes[node_id]
        }
    
    def remove_node(self, node_id: str) -> None:
        """Remove a node and all its edges from the graph."""
        if node_id in self.graph:
            self.graph.remove_node(node_id)
            self._cycle_cache.clear()
    
    def get_subgraph(self, node_ids: Set[str]) -> 'DependencyGraph':
        """Create a new DependencyGraph containing only the specified nodes and their edges."""
        subgraph = DependencyGraph()
        
        # Add nodes
        for node_id in node_ids:
            if node_id in self.graph:
                node_data = self.get_node_info(node_id)
                if node_data:
                    subgraph.add_node(Node(
                        id=node_id,
                        component_type=node_data['component_type'],
                        language=node_data['language'],
                        file_path=node_data['file_path'],
                        metadata=node_data['metadata']
                    ))
        
        # Add edges between included nodes
        for from_id in node_ids:
            for to_id in self.get_dependencies(from_id):
                if to_id in node_ids:
                    subgraph.add_edge(from_id, to_id)
        
        return subgraph
    
    def get_connected_components(self) -> List[Set[str]]:
        """Get all connected components in the graph."""
        return [set(component) for component in nx.weakly_connected_components(self.graph)]
    
    def get_entry_points(self) -> Set[str]:
        """Get all nodes that have no incoming edges (entry points)."""
        return {n for n, d in self.graph.in_degree() if d == 0}
    
    def get_exit_points(self) -> Set[str]:
        """Get all nodes that have no outgoing edges (exit points)."""
        return {n for n, d in self.graph.out_degree() if d == 0}
    
    def validate(self) -> List[str]:
        """Validate the graph structure and return any issues found."""
        issues = []
        
        # Check for missing nodes referenced in edges
        for from_node, to_node in self.graph.edges():
            if from_node not in self.graph.nodes:
                issues.append(f"Edge references missing from_node: {from_node}")
            if to_node not in self.graph.nodes:
                issues.append(f"Edge references missing to_node: {to_node}")
        
        # Check for cycles
        cycles = self.detect_cycles()
        if cycles:
            issues.append(f"Found {len(cycles)} dependency cycles")
            for cycle in cycles[:3]:  # Report first 3 cycles
                issues.append(f"Cycle: {' -> '.join(cycle)}")
        
        # Check for isolated nodes
        isolated = list(nx.isolates(self.graph))
        if isolated:
            issues.append(f"Found {len(isolated)} isolated nodes")
        
        return issues
