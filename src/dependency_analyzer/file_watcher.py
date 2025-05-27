"""File watcher for monitoring and automatically parsing repository changes."""

import os
import time
import logging
from typing import Dict, Set, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .ast_parser import DependencyParser
from .cpp_parser import CppParser

logger = logging.getLogger(__name__)

class RepoWatcher(FileSystemEventHandler):
    """Watches repository for file changes and triggers parsing."""
    
    def __init__(self, repo_path: str, dependency_parser: DependencyParser):
        self.repo_path = os.path.abspath(repo_path)
        self.dependency_parser = dependency_parser
        self.observer = Observer()
        self.last_modified: Dict[str, float] = {}
        self.pending_files: Set[str] = set()
        self._debounce_time = 1.0  # seconds
        
    def start(self):
        """Start watching the repository."""
        self.observer.schedule(self, self.repo_path, recursive=True)
        self.observer.start()
        logger.info(f"Started watching repository: {self.repo_path}")
        
    def stop(self):
        """Stop watching the repository."""
        self.observer.stop()
        self.observer.join()
        logger.info("Stopped watching repository")
        
    def on_modified(self, event):
        if event.is_directory:
            return
            
        file_path = event.src_path
        if self._should_process_file(file_path):
            current_time = time.time()
            self.last_modified[file_path] = current_time
            self.pending_files.add(file_path)
            
    def _should_process_file(self, file_path: str) -> bool:
        """Check if the file should be processed."""
        if not os.path.exists(file_path):
            return False
            
        ext = os.path.splitext(file_path)[1].lower()
        return ext in {'.py', '.cpp', '.hpp', '.h', '.cxx', '.cc'}
        
    def process_pending_files(self):
        """Process any pending file changes."""
        current_time = time.time()
        files_to_process = set()
        
        # Check which files are ready for processing
        for file_path in self.pending_files.copy():
            last_mod = self.last_modified.get(file_path, 0)
            if current_time - last_mod >= self._debounce_time:
                files_to_process.add(file_path)
                self.pending_files.remove(file_path)
                
        if files_to_process:
            logger.info(f"Processing {len(files_to_process)} changed files")
            self.dependency_parser.parse_files(files_to_process)
            
    def get_pending_count(self) -> int:
        """Get number of files pending processing."""
        return len(self.pending_files)
