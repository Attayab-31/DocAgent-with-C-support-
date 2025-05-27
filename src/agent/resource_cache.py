"""Resource caching and management for parallel processing."""
import threading
import time
from typing import Dict, Any, Optional, List
import logging
import weakref

logger = logging.getLogger(__name__)

class ResourceCache:
    """Manages caching of processed components and their results."""
    
    def __init__(self, max_size: int = 1000, ttl: int = 3600):
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._max_size = max_size
        self._ttl = ttl  # Time to live in seconds
        self._lock = threading.Lock()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._running = True
        self._cleanup_thread.start()
        
        # Weak references for memory management
        self._weak_refs: Dict[str, weakref.ref] = {}
    
    def get(self, key: str) -> Optional[Any]:
        """Retrieve a cached item if it exists and is not expired."""
        with self._lock:
            if key in self._cache:
                if self._is_valid(key):
                    self._timestamps[key] = time.time()
                    return self._cache[key]
                else:
                    self._remove(key)
        return None
    
    def put(self, key: str, value: Any) -> None:
        """Store an item in the cache with timestamp."""
        with self._lock:
            if len(self._cache) >= self._max_size:
                self._evict_oldest()
            
            self._cache[key] = value
            self._timestamps[key] = time.time()
            
            # Store weak reference if possible
            try:
                self._weak_refs[key] = weakref.ref(value)
            except TypeError:
                # Object cannot be weakly referenced
                pass
    
    def _is_valid(self, key: str) -> bool:
        """Check if a cached item is still valid (not expired)."""
        timestamp = self._timestamps.get(key)
        if timestamp is None:
            return False
        return (time.time() - timestamp) < self._ttl
    
    def _evict_oldest(self) -> None:
        """Remove the oldest cached item."""
        if not self._timestamps:
            return
        
        oldest_key = min(self._timestamps.items(), key=lambda x: x[1])[0]
        self._remove(oldest_key)
    
    def _remove(self, key: str) -> None:
        """Remove an item from all cache structures."""
        self._cache.pop(key, None)
        self._timestamps.pop(key, None)
        self._weak_refs.pop(key, None)
    
    def _cleanup_loop(self) -> None:
        """Continuously clean up expired cache entries."""
        while self._running:
            time.sleep(60)  # Check every minute
            self._cleanup()
    
    def _cleanup(self) -> None:
        """Remove expired cache entries."""
        with self._lock:
            current_time = time.time()
            expired_keys = [
                key for key, timestamp in self._timestamps.items()
                if (current_time - timestamp) >= self._ttl
            ]
            for key in expired_keys:
                self._remove(key)
    
    def clear(self) -> None:
        """Clear all cached items."""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()
            self._weak_refs.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                'size': len(self._cache),
                'max_size': self._max_size,
                'ttl': self._ttl,
                'oldest_entry_age': min(time.time() - t for t in self._timestamps.values()) if self._timestamps else 0,
            }
    
    def __del__(self):
        """Cleanup on deletion."""
        self._running = False
        self.clear()
