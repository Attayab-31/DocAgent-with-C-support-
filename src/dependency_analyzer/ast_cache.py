"""Efficient AST cache with memory management for large codebases."""

import ast
import weakref
from typing import Dict, Optional
import logging
import gc

logger = logging.getLogger(__name__)

class ASTCache:
    """Memory-efficient cache for AST trees with automatic cleanup."""
    
    def __init__(self, max_cache_size: int = 50):
        self._cache: Dict[str, weakref.ref] = {}
        self._max_size = max_cache_size
        
    def get(self, file_path: str) -> Optional[ast.AST]:
        """Get AST for a file from cache if available."""
        if file_path in self._cache:
            tree_ref = self._cache[file_path]
            tree = tree_ref()
            if tree is not None:
                return tree
            # Clean up expired reference
            del self._cache[file_path]
        return None
        
    def put(self, file_path: str, tree: ast.AST) -> None:
        """Add AST to cache with memory management."""
        # Clean up if cache is too large
        if len(self._cache) >= self._max_size:
            # Remove oldest entries
            oldest = list(self._cache.keys())[:len(self._cache) // 4]
            for key in oldest:
                del self._cache[key]
            # Force garbage collection
            gc.collect()
        
        # Store new tree with weak reference
        self._cache[file_path] = weakref.ref(tree)
        
    def clear(self) -> None:
        """Clear the cache and force garbage collection."""
        self._cache.clear()
        gc.collect()
        
    def __len__(self) -> int:
        """Get number of valid entries in cache."""
        return sum(1 for ref in self._cache.values() if ref() is not None)