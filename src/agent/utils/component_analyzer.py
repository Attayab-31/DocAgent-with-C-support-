"""Utility functions for analyzing code components."""

import ast
from typing import Optional, Tuple

def analyze_component(ast_node: ast.AST, dependency_path: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Analyze a code component to determine its type and associated class.
    
    Args:
        ast_node: The AST node representing the component
        dependency_path: Optional dependency path string (e.g. "Calculator.add")
        
    Returns:
        Tuple of (component_type, class_name) where:
            component_type: One of "class", "method", "function"
            class_name: Name of containing class for methods, None otherwise
    """
    # Check by AST node type first
    if isinstance(ast_node, ast.ClassDef):
        return "class", None
    elif isinstance(ast_node, ast.FunctionDef):
        # Look at parent node to determine if it's a method
        if getattr(ast_node, 'parent', None) and isinstance(ast_node.parent, ast.ClassDef):
            return "method", ast_node.parent.name
        return "function", None
        
    # Fall back to analyzing dependency path
    if dependency_path and '.' in dependency_path:
        parts = dependency_path.split('.')
        if len(parts) == 2:
            return "method", parts[0]
            
    # Default case
    return "function", None
