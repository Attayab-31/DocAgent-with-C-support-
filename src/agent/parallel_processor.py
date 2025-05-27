# Copyright (c) Meta Platforms, Inc. and affiliates
import multiprocessing
import queue
import time
import gc
from typing import Dict, List, Optional, Any, Tuple, Set
from pathlib import Path
import ast
import logging
import os
from .orchestrator import Orchestrator
from .dependency_graph import DependencyGraph, Node
from ..dependency_analyzer import CodeComponent

logger = logging.getLogger(__name__)

from .resource_cache import ResourceCache

class ResourceMonitor:
    """Monitors and manages system resources during parallel processing."""
    
    def __init__(self):
        self.warning_threshold = 80  # Percentage
        self.critical_threshold = 90  # Percentage
        self._import_psutil()
        self.cache = ResourceCache()  # Initialize resource cache
    
    def _import_psutil(self):
        """Try to import psutil for resource monitoring."""
        try:
            import psutil
            self.psutil = psutil
            self.has_psutil = True
        except ImportError:
            logger.warning("psutil not available - resource monitoring will be limited")
            self.has_psutil = False
            
    def clear_cache(self):
        """Clear the resource cache."""
        self.cache.clear()
    
    def check_resources(self) -> Tuple[bool, str]:
        """Check system resources and return status with message."""
        if not self.has_psutil:
            return True, "Resource monitoring not available"
        
        try:
            # Get current process
            process = self.psutil.Process()
            
            # Check memory usage
            memory_percent = process.memory_percent()
            system_memory = self.psutil.virtual_memory()
            
            # Check CPU usage
            cpu_percent = self.psutil.cpu_percent(interval=0.1)
            
            status_msg = f"Memory: {memory_percent:.1f}%, CPU: {cpu_percent:.1f}%"
            
            # Critical threshold reached
            if memory_percent > self.critical_threshold or system_memory.percent > self.critical_threshold:
                self._perform_emergency_cleanup()
                return False, f"Critical resource usage - {status_msg}"
            
            # Warning threshold reached
            if memory_percent > self.warning_threshold or system_memory.percent > self.warning_threshold:
                self._perform_cleanup()
                return True, f"High resource usage - {status_msg}"
            
            return True, f"Resources OK - {status_msg}"
            
        except Exception as e:
            logger.error(f"Error monitoring resources: {str(e)}")
            return True, "Resource monitoring error"
    
    def _perform_cleanup(self):
        """Perform basic cleanup when warning threshold is reached."""
        gc.collect()
    
    def _perform_emergency_cleanup(self):
        """Perform emergency cleanup when critical threshold is reached."""
        self._perform_cleanup()
        if self.has_psutil:
            try:
                process = self.psutil.Process()
                # Clear process memory
                if hasattr(process, "memory_info"):
                    process.memory_info().clear()
            except Exception as e:
                logger.error(f"Error in emergency cleanup: {str(e)}")
                
class ParallelProcessor:
    """Handles parallel processing of docstring generation for independent components."""
    
    def __init__(self, repo_path: str, config_path: Optional[str] = None, max_workers: Optional[int] = None):
        self.repo_path = repo_path
        self.config_path = config_path
        
        # Initialize components
        self.resource_monitor = ResourceMonitor()
        self.dependency_graph = DependencyGraph()
        
        # Get optimal worker count
        self.max_workers = self._get_optimal_worker_count(max_workers)
        
        # Create manager for shared resources
        self._manager = multiprocessing.Manager()
        self._lock = self._manager.Lock()
        self._python_semaphore = self._manager.Semaphore(self.max_workers)
        
        # Batch sizes with adaptive adjustment
        self._base_batch_size = 5
        self._python_batch_size = self._base_batch_size * 2
        self._cpp_batch_size = self._base_batch_size
        
        # Performance monitoring
        self._processing_times: Dict[str, float] = {}
        self._error_counts: Dict[str, int] = {}
        
        # Resource thresholds
        self.memory_threshold = 80  # Percentage
        self.cpu_threshold = 90  # Percentage
        self.has_psutil = self.resource_monitor.has_psutil
        
        # Initialize cache for processed results
        self._result_cache = self.resource_monitor.cache

    def _get_optimal_worker_count(self, max_workers: Optional[int]) -> int:
        """Calculate optimal number of workers based on system resources."""
        try:
            import psutil
            cpu_count = psutil.cpu_count(logical=False) or psutil.cpu_count()
            total_memory_gb = psutil.virtual_memory().total / (1024 ** 3)
            
            # Base count on CPU cores
            optimal_workers = cpu_count - 1 if cpu_count > 1 else 1
            
            # Adjust based on memory (assume ~2GB per worker)
            memory_based_workers = int(total_memory_gb / 2)
            optimal_workers = min(optimal_workers, memory_based_workers)
            
            # Allow override but cap at reasonable maximum
            if max_workers is not None:
                optimal_workers = min(max_workers, optimal_workers)
            
            return max(1, optimal_workers)
        
        except ImportError:
            # Fallback if psutil not available
            cpu_count = multiprocessing.cpu_count()
            return max(1, cpu_count - 1)
        
        self.memory_threshold = memory_threshold
        self.cpu_threshold = cpu_threshold
        self.has_psutil = self._check_psutil()
    
    @staticmethod
    def _check_psutil() -> bool:
        """Check if psutil is available."""
        try:
            import psutil
            return True
        except ImportError:
            return False
    
    def get_resource_usage(self) -> tuple:
        """Get current CPU and memory usage."""
        if not self.has_psutil:
            return None, None
        
        try:
            import psutil
            process = psutil.Process()
            return process.cpu_percent(), process.memory_percent()
        except Exception as e:
            logger.error(f"Error getting resource usage: {str(e)}")
            return None, None
    
    def should_throttle(self) -> bool:
        """Check if processing should be throttled."""
        if not self.has_psutil:
            return False
        
        cpu_percent, memory_percent = self.get_resource_usage()
        if cpu_percent is None or memory_percent is None:
            return False
        
        return cpu_percent > self.cpu_threshold or memory_percent > self.memory_threshold
    
    def get_recommended_batch_sizes(self, current_python_size: int, current_cpp_size: int) -> tuple:
        """Get recommended batch sizes based on resource usage."""
        if not self.should_throttle():
            return current_python_size, current_cpp_size
        
        # Reduce batch sizes based on resource usage
        new_python_size = max(2, current_python_size // 2)
        new_cpp_size = max(1, current_cpp_size // 2)
        
        return new_python_size, new_cpp_size
    
    def suggest_worker_count(self, max_workers: int) -> int:
        """Suggest worker count based on system resources."""
        if not self.has_psutil:
            return max_workers
        
        try:
            import psutil
            
            # Get available memory
            memory = psutil.virtual_memory()
            available_gb = memory.available / (1024 ** 3)
            
            # Estimate memory per worker (assume 2GB per worker)
            memory_based_workers = max(1, int(available_gb / 2))
            
            # Get CPU count
            cpu_count = psutil.cpu_count(logical=False) or psutil.cpu_count()
            
            # Return the minimum of memory-based and CPU-based worker counts
            return min(max_workers, memory_based_workers, max(1, cpu_count - 1))
            
        except Exception as e:
            logger.error(f"Error suggesting worker count: {str(e)}")
            return max_workers
    
    def process_level(self, components: Dict[str, CodeComponent], ast_trees: Dict[str, ast.AST],
                     dependency_graph: Dict[str, List[str]], level_components: List[str]) -> Dict[str, str]:
        """Process all components at a given dependency level in parallel."""
        results = {}
        component_data = []

        # Convert AST trees to source code strings to make them picklable
        ast_sources = {}
        for comp_id, tree in ast_trees.items():
            try:
                ast_sources[comp_id] = ast.unparse(tree)
            except:
                if comp_id in components:
                    ast_sources[comp_id] = components[comp_id].source_code

        # Prepare component data for processing
        for comp_id in level_components:
            component = components.get(comp_id)
            if not component:
                continue

            data = {
                'component_id': comp_id,
                'source_code': component.source_code,
                'file_path': component.file_path,
                'language': component.language,
                'repo_path': self.repo_path,
                'config_path': self.config_path,
                'full_source': ast_sources.get(comp_id, component.source_code),
                'depends_on': dependency_graph.get(comp_id, [])
            }
            component_data.append(data)

        # Process components in parallel
        try:
            with multiprocessing.Pool(self.max_workers) as pool:
                for batch_results in pool.imap_unordered(self._process_single_component_wrapper, component_data):
                    if batch_results:
                        comp_id, docstring = batch_results
                        results[comp_id] = docstring

        except Exception as e:
            logger.error(f"Error in parallel processing: {str(e)}")
            # Fallback to sequential processing
            for data in component_data:
                try:
                    docstring = self._process_single_component(data)
                    results[data['component_id']] = docstring
                except Exception as comp_error:
                    logger.error(f"Error processing component {data['component_id']}: {str(comp_error)}")
                    results[data['component_id']] = ""

        return results

    @staticmethod
    def _process_single_component_wrapper(data: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """Wrapper for processing single components to handle pickling."""
        try:
            orchestrator = Orchestrator(data['repo_path'], data['config_path'])
            docstring = orchestrator.process(
                focal_component=data['source_code'],
                file_path=data['file_path'],
                ast_node=None,
                ast_tree=None,
                dependency_graph={'depends_on': data['depends_on']},
                focal_node_dependency_path=data['component_id']
            )
            return (data['component_id'], docstring)
        except Exception as e:
            logger.error(f"Error in component wrapper for {data['component_id']}: {str(e)}")
            return None
        finally:
            if 'orchestrator' in locals():
                try:
                    orchestrator.clear_memory()
                except:
                    pass
    
    def _process_batch(self, batch: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
        """Process a batch of components with resource monitoring."""
        results = []
        orchestrator = None
        
        try:
            orchestrator = Orchestrator(self.repo_path, self.config_path)
            
            for data in batch:
                try:
                    if not self._check_resources():
                        time.sleep(0.5)  # Brief pause if resources are constrained
                    
                    docstring = orchestrator.process(
                        focal_component=data['source_code'],
                        file_path=data['file_path'],
                        ast_node=None,
                        ast_tree=None,
                        dependency_graph={'depends_on': data['depends_on']},
                        focal_node_dependency_path=data['component_id']
                    )
                    results.append((data['component_id'], docstring))
                    
                except Exception as e:
                    logger.error(f"Error processing component {data['component_id']}: {str(e)}")
                    results.append((data['component_id'], ""))
                    self._error_counts[data['component_id']] = self._error_counts.get(data['component_id'], 0) + 1
        
        finally:
            if orchestrator:
                try:
                    orchestrator.clear_memory()
                except:
                    pass
        
        return results
    
    def _process_single_component(self, data: Dict[str, Any]) -> str:
        """Process a single component (used as fallback)."""
        orchestrator = None
        try:
            orchestrator = Orchestrator(self.repo_path, self.config_path)
            return orchestrator.process(
                focal_component=data['source_code'],
                file_path=data['file_path'],
                ast_node=None,
                ast_tree=None,
                dependency_graph={'depends_on': data['depends_on']},
                focal_node_dependency_path=data['component_id']
            )
        except Exception as e:
            logger.error(f"Error in sequential processing for {data['component_id']}: {str(e)}")
            return ""
        finally:
            if orchestrator:
                try:
                    orchestrator.clear_memory()
                except:
                    pass

    def _check_resources(self) -> bool:
        """Monitor system resources and adjust processing parameters if needed.
        
        Returns:
            bool: True if resources are available, False if processing should pause
        """
        try:
            import psutil
            process = psutil.Process()
            
            # Check memory usage
            memory_percent = process.memory_percent()
            system_memory = psutil.virtual_memory()
            
            # If memory usage is too high, trigger garbage collection
            if memory_percent > 80 or system_memory.percent > 90:
                import gc
                gc.collect()
                
                # If still too high after GC, return False to suggest pausing
                if process.memory_percent() > 80 or psutil.virtual_memory().percent > 90:
                    logger.warning("High memory usage detected, suggesting processing pause")
                    return False
            
            # Check CPU usage
            cpu_percent = process.cpu_percent()
            if cpu_percent > 95:
                logger.warning("High CPU usage detected, suggesting processing pause")
                return False
            
            return True
        
        except ImportError:
            # If psutil is not available, assume resources are available
            return True
        except Exception as e:
            logger.error(f"Error checking resources: {str(e)}")
            return True  # Continue processing on error
    
    def _cleanup_resources(self):
        """Clean up resources and attempt to free memory."""
        import gc
        import sys
        
        try:
            # Force garbage collection
            gc.collect()
            
            # Clear any caches if psutil is available
            try:
                import psutil
                process = psutil.Process()
                
                # Clear memory maps
                if hasattr(process, 'memory_maps'):
                    process.memory_maps().clear()
                
                # Suggest memory cleanup to OS
                if sys.platform.startswith('win'):
                    import ctypes
                    ctypes.windll.kernel32.SetProcessWorkingSetSize(-1, -1)
                else:
                    import resource
                    resource.setrlimit(resource.RLIMIT_AS, (-1, -1))
            except ImportError:
                pass
            
            # Clear any module-level caches
            if hasattr(sys, 'intern'):
                sys.intern.cache_clear() if hasattr(sys.intern, 'cache_clear') else None
        except Exception as e:
            logger.error(f"Error during resource cleanup: {str(e)}")
        
    def _recover_from_error(self, error: Exception, component_id: str) -> None:
        """Handle errors during component processing and attempt recovery."""
        logger.error(f"Error processing component {component_id}: {str(error)}")
        
        try:
            # Clean up resources
            self._cleanup_resources()
            
            # Reduce batch sizes temporarily
            self._python_batch_size = max(2, self._python_batch_size // 2)
            self._cpp_batch_size = max(1, self._cpp_batch_size // 2)
            
            # Log recovery attempt
            logger.info(f"Attempting recovery - adjusted batch sizes: Python={self._python_batch_size}, C++={self._cpp_batch_size}")
            
            # Wait briefly to allow system to stabilize
            import time
            time.sleep(0.5)
            
        except Exception as recovery_error:
            logger.error(f"Error during recovery attempt: {str(recovery_error)}")
        
        # Reset batch sizes gradually over time
        def _reset_batch_sizes():
            time.sleep(30)  # Wait before attempting to reset
            self._python_batch_size = self._base_batch_size * 2
            self._cpp_batch_size = max(2, self._base_batch_size // 2)
            logger.info("Reset batch sizes to default values")
        
        import threading
        threading.Thread(target=_reset_batch_sizes, daemon=True).start()

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics for the processor."""
        stats = {
            'average_processing_time': sum(self._processing_times.values()) / len(self._processing_times) if self._processing_times else 0,
            'total_errors': sum(self._error_counts.values()),
            'components_processed': len(self._processing_times),
            'cache_stats': self._result_cache.get_stats(),
            'current_batch_sizes': {
                'python': self._python_batch_size,
                'cpp': self._cpp_batch_size
            }
        }
        
        if self.has_psutil:
            cpu_percent, memory_percent = self.get_resource_usage()
            stats.update({
                'cpu_usage': cpu_percent,
                'memory_usage': memory_percent
            })
        
        return stats

    def optimize_batch_sizes(self) -> None:
        """Optimize batch sizes based on performance statistics."""
        if not self._processing_times:
            return

        avg_time = sum(self._processing_times.values()) / len(self._processing_times)
        error_rate = sum(self._error_counts.values()) / len(self._processing_times) if self._processing_times else 0

        # Adjust batch sizes based on performance
        if error_rate > 0.1:  # More than 10% error rate
            self._python_batch_size = max(2, self._python_batch_size // 2)
            self._cpp_batch_size = max(1, self._cpp_batch_size // 2)
        elif avg_time > 5.0:  # If average processing time is too high
            self._python_batch_size = max(2, int(self._python_batch_size * 0.8))
            self._cpp_batch_size = max(1, int(self._cpp_batch_size * 0.8))
        elif avg_time < 1.0 and error_rate < 0.05:  # If processing is fast and stable
            self._python_batch_size = min(20, int(self._python_batch_size * 1.2))
            self._cpp_batch_size = min(10, int(self._cpp_batch_size * 1.2))

    def clear_caches(self) -> None:
        """Clear all caches and reset performance statistics."""
        self._result_cache.clear()
        self._processing_times.clear()
        self._error_counts.clear()
        self.resource_monitor.clear_cache()

    def get_dependency_levels(self, dependency_graph: Dict[str, List[str]]) -> List[List[str]]:
        """Get components grouped by dependency levels using the DependencyGraph."""
        # Convert dictionary representation to DependencyGraph
        for node_id, dependencies in dependency_graph.items():
            if node_id not in self.dependency_graph.graph:
                self.dependency_graph.add_node(Node(
                    id=node_id,
                    component_type='unknown',
                    language='unknown',
                    file_path='',
                    metadata={}
                ))
            for dep in dependencies:
                if dep not in self.dependency_graph.graph:
                    self.dependency_graph.add_node(Node(
                        id=dep,
                        component_type='unknown',
                        language='unknown',
                        file_path='',
                        metadata={}
                    ))
                self.dependency_graph.add_edge(node_id, dep)

        return self.dependency_graph.get_dependency_levels()

    def handle_resource_pressure(self) -> None:
        """Handle resource pressure by cleaning up and adjusting parameters."""
        self._cleanup_resources()
        self.optimize_batch_sizes()
        
        # If still under pressure, take more aggressive measures
        if not self._check_resources():
            self.clear_caches()
            self._python_batch_size = max(2, self._python_batch_size // 2)
            self._cpp_batch_size = max(1, self._cpp_batch_size // 2)
            time.sleep(2)  # Allow system to stabilize

    def chunk_components(self, components: Dict[str, CodeComponent], chunk_size: int = 50) -> List[Dict[str, CodeComponent]]:
        """Split components into manageable chunks for processing large repositories."""
        items = list(components.items())
        return [{k: v for k, v in items[i:i + chunk_size]} for i in range(0, len(items), chunk_size)]

    def process_large_repository(self, components: Dict[str, CodeComponent], ast_trees: Dict[str, ast.AST],
                               dependency_graph: Dict[str, List[str]]) -> Dict[str, str]:
        """Process a large repository by chunking and optimizing memory usage."""
        all_results = {}
        chunks = self.chunk_components(components)
        total_chunks = len(chunks)
        
        logger.info(f"Processing large repository in {total_chunks} chunks")
        
        for chunk_index, chunk in enumerate(chunks, 1):
            logger.info(f"Processing chunk {chunk_index}/{total_chunks} ({len(chunk)} components)")
            
            # Get relevant subset of ASTs and dependencies
            chunk_ast_trees = {k: v for k, v in ast_trees.items() if k in chunk}
            chunk_deps = {k: v for k, v in dependency_graph.items() if k in chunk}
            
            # Get dependency levels for this chunk
            levels = self.get_dependency_levels(chunk_deps)
            
            # Process each level in the chunk
            for level in levels:
                level_components = [comp_id for comp_id in level if comp_id in chunk]
                if not level_components:
                    continue
                
                # Process the level with resource monitoring
                try:
                    results = self.process_level(chunk, chunk_ast_trees, chunk_deps, level_components)
                    all_results.update(results)
                    
                    # Perform cleanup after each level
                    self.handle_resource_pressure()
                    
                except Exception as e:
                    logger.error(f"Error processing chunk {chunk_index}, level {level}: {str(e)}")
                    # Continue with next level despite errors
                    continue
            
            # Force cleanup between chunks
            self._cleanup_resources()
            gc.collect()
            
            # Adjust batch sizes based on performance
            self.optimize_batch_sizes()
            
            # Brief pause to allow system to stabilize
            time.sleep(0.1)
        
        return all_results

    def estimate_memory_requirements(self, components: Dict[str, CodeComponent]) -> float:
        """Estimate memory requirements in GB for processing the components."""
        total_size = 0
        
        # Estimate source code size
        for component in components.values():
            total_size += len(component.source_code) * 2  # Unicode characters
            
            # Account for AST and other metadata
            total_size += 1024  # Base overhead per component
        
        # Account for processing overhead (3x base size)
        total_size *= 3
        
        # Convert to GB
        return total_size / (1024 * 1024 * 1024)

    def adjust_for_large_repo(self, components: Dict[str, CodeComponent]) -> None:
        """Adjust processing parameters for large repositories."""
        estimated_gb = self.estimate_memory_requirements(components)
        
        # Adjust batch sizes based on repository size
        if estimated_gb > 4.0:
            self._python_batch_size = max(2, self._python_batch_size // 2)
            self._cpp_batch_size = max(1, self._cpp_batch_size // 2)
        
        # Adjust worker count based on repository size
        if self.has_psutil:
            try:
                import psutil
                available_gb = psutil.virtual_memory().available / (1024 ** 3)
                if estimated_gb > available_gb / 2:
                    self.max_workers = max(1, self.max_workers // 2)
            except:
                pass
