# Copyright (c) Meta Platforms, Inc. and affiliates
"""
C++ code parser that extracts dependency information between code components.

This module identifies includes and references between C++ code components (functions, classes, methods)
and builds a dependency graph for topological sorting.
"""

import os
import re
import json
import sys
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Any, Union
from pathlib import Path
from .cpp_docstring import has_docstring

logger = logging.getLogger(__name__)

# Initialize clang
CLANG_AVAILABLE = False
CursorKind = None
TokenKind = None
Index = None

# Try to load libclang from environment variable or common locations
try:
    import clang.cindex
    libclang_path = os.environ.get('LIBCLANG_PATH')
    tried_paths = []
    if libclang_path and os.path.exists(libclang_path):
        clang.cindex.Config.set_library_file(libclang_path)
        tried_paths.append(libclang_path)
    else:
        # Try common Windows locations
        possible_paths = [
            r"C:\Program Files\LLVM\bin\libclang.dll",
            r"C:\msys64\mingw64\bin\libclang.dll",
            r"C:\Program Files (x86)\LLVM\bin\libclang.dll",
        ]
        for path in possible_paths:
            if os.path.exists(path):
                clang.cindex.Config.set_library_file(path)
                tried_paths.append(path)
                break
    from clang.cindex import CursorKind, TokenKind, Index, TranslationUnit
    # Try to create an Index to verify clang works
    try:
        _test_index = Index.create()
        CLANG_AVAILABLE = True
        logger.info(f"Successfully initialized libclang from: {tried_paths[-1] if tried_paths else 'default search'}")
    except Exception as e:
        logger.error(f"libclang found but could not create Index: {e}")
        CLANG_AVAILABLE = False
except ImportError:
    logger.error("clang module not found. Please install: pip install clang")
    CLANG_AVAILABLE = False
except Exception as e:
    logger.error(f"Error initializing clang: {e}")
    CLANG_AVAILABLE = False

def _extract_docstring(cursor) -> Optional[str]:
    """Extract docstring from comments before the declaration."""
    if not CLANG_AVAILABLE:
        # Fallback to using the has_docstring function
        source_code = cursor.translation_unit.spelling  # Assuming this retrieves the source code
        if has_docstring(source_code):
            return "Detected docstring using fallback logic."
        return None

    try:
        start_loc = cursor.extent.start
        if not start_loc.file:
            return None

        # Find comments before the declaration
        tokens = []
        prev_line = -1

        # Get all tokens before our cursor's start line
        tu = cursor.translation_unit
        for token in tu.cursor.get_tokens():
            if not token.location.file:
                continue

            if token.location.file.name == start_loc.file.name:
                if token.extent.end.line < start_loc.line:
                    # Only consider comment tokens
                    if token.kind.name == 'COMMENT':
                        # Check if this is part of a continuous comment block
                        if prev_line == -1 or token.extent.start.line == prev_line + 1:
                            # Store the comment and its line number
                            tokens.append((token.spelling, token.extent.start.line))
                        else:
                            # Start a new comment block
                            tokens = [(token.spelling, token.extent.start.line)]
                        prev_line = token.extent.end.line

                elif token.extent.start.line >= start_loc.line:
                    break

        if not tokens:
            return None

        # Process the comments
        docstring_parts = []
        for comment, _ in tokens:
            # Clean up the comment markers and whitespace
            cleaned = comment.strip()
            if cleaned.startswith('/*'):
                cleaned = cleaned[2:]
            if cleaned.endswith('*/'):
                cleaned = cleaned[:-2]
            if cleaned.startswith('//'):
                cleaned = cleaned[2:]
            cleaned = cleaned.strip()

            if cleaned:
                docstring_parts.append(cleaned)

        return '\n'.join(docstring_parts) if docstring_parts else None

    except Exception as e:
        logger.error(f"Error extracting docstring: {e}")
        return None

@dataclass
class CppComponent:
    id: str
    component_type: str  # 'function', 'class', or 'method'  
    file_path: str
    relative_path: str
    depends_on: Set[str] = field(default_factory=set)
    start_line: int = 0
    end_line: int = 0
    has_docstring: bool = False
    docstring: str = ""
    language: str = "cpp"
    source_code: str = ""  # Added source_code attribute
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'component_type': self.component_type,
            'file_path': self.file_path,
            'relative_path': self.relative_path,
            'depends_on': list(self.depends_on),
            'start_line': self.start_line,
            'end_line': self.end_line,
            'has_docstring': self.has_docstring,
            'docstring': self.docstring,
            'language': self.language,
            'source_code': self.source_code
        }
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CppComponent':
        component = cls(
            id=data['id'],
            component_type=data['component_type'],
            file_path=data['file_path'],
            relative_path=data['relative_path']
        )
        component.depends_on = set(data['depends_on'])
        component.start_line = data['start_line']
        component.end_line = data['end_line']
        component.has_docstring = data['has_docstring']
        component.docstring = data['docstring']
        component.language = data.get('language', 'cpp')
        component.source_code = data.get('source_code', '')
        return component

class CppParser:
    """Parser for C++ source files using libclang."""
    
    def __init__(self, repo_path: str):
        """Initialize the C++ parser.
        
        Args:
            repo_path: Path to the repository root
        """
        self.repo_path = repo_path
        self._init_clang()
        self.components = {}
        
    def _init_clang(self):
        """Initialize clang with system includes."""
        if not CLANG_AVAILABLE:
            return
            
        # Add common system include paths
        self.clang_args = [
            "-xc++",  # Force C++ mode
            "-std=c++14",  # Use C++14 standard
            "-I/usr/include",
            "-I/usr/local/include",
        ]
        
        # Add MSVC include paths on Windows
        if os.name == 'nt':
            msvc_paths = [
                "C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.30.30705/include",
                "C:/Program Files (x86)/Windows Kits/10/Include/10.0.19041.0/ucrt",
            ]
            for path in msvc_paths:
                if os.path.exists(path):
                    self.clang_args.extend(["-I", path])

    def parse_file(self, file_path: str, repo_path: str = None) -> List[Dict[str, Any]]:
        """
        Parse a C++ file and extract components and their dependencies.
        
        Args:
            file_path: Path to the C++ file to parse
            repo_path: Optional path to the repository root. If not provided,
                      will use the parent directory of the file.
        
        Returns:
            List of components found in the file
        """
        if not CLANG_AVAILABLE:
            logger.error("Clang not available - cannot parse C++ files")
            return []

        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return []
            
        # Calculate relative path from repo root
        if repo_path is None:
            repo_path = os.path.dirname(file_path)
        relative_path = os.path.relpath(file_path, repo_path)
        
        try:
            index = Index.create()
            tu = index.parse(file_path)
            if not tu:
                logger.error(f"Failed to parse {file_path}")
                return []
                
            components = []
            for cursor in tu.cursor.walk_preorder():
                if cursor.kind in [CursorKind.FUNCTION_DECL, CursorKind.CXX_METHOD, 
                                 CursorKind.CLASS_DECL, CursorKind.CONSTRUCTOR]:
                    component = self._extract_component(cursor, file_path, relative_path)
                    if component:
                        components.append(component)
                        
            return components
            
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {str(e)}")
            return []

    def _process_node(self, cursor, file_path: str, relative_path: str):
        """Process a node in the AST and identify components."""
        try:
            if cursor.location.file and cursor.location.file.name == file_path:
                # Handle functions
                if cursor.kind == CursorKind.FUNCTION_DECL:
                    self._add_function(cursor, file_path, relative_path)
                
                # Handle classes
                elif cursor.kind == CursorKind.CLASS_DECL:
                    self._add_class(cursor, file_path, relative_path)
                    
                    # Process class methods and members
                    self._process_class_members(cursor, cursor.spelling, file_path, relative_path)

            # Recursively process children
            for child in cursor.get_children():
                self._process_node(child, file_path, relative_path)

        except Exception as e:
            logger.error(f"Error processing node: {e}")

    def _process_class_members(self, cursor, class_name: str, file_path: str, relative_path: str):
        """Process class members (methods and variables) recursively."""
        for child in cursor.get_children():
            if child.kind in (CursorKind.FIELD_DECL, CursorKind.VAR_DECL):
                # Handle class member variables
                self._add_member_variable(child, class_name, file_path, relative_path)
            elif child.kind in (CursorKind.CXX_METHOD, CursorKind.CONSTRUCTOR, CursorKind.DESTRUCTOR):
                # Handle class methods
                self._add_method(child, class_name, file_path, relative_path)

    def _add_function(self, cursor, file_path: str, relative_path: str):
        """Add a function component."""
        component = CppComponent(
            id=f"{relative_path}::{cursor.spelling}::FUNCTION_DECL",
            component_type="function",
            file_path=file_path,
            relative_path=relative_path
        )
        self._set_component_details(component, cursor)
        self.components[component.id] = component

    def _add_class(self, cursor, file_path: str, relative_path: str):
        """Add a class component."""
        component = CppComponent(
            id=f"{relative_path}::{cursor.spelling}::CLASS_DECL",
            component_type="class",
            file_path=file_path,
            relative_path=relative_path
        )
        self._set_component_details(component, cursor)
        self.components[component.id] = component

    def _add_method(self, cursor, class_name: str, file_path: str, relative_path: str):
        """Add a method component."""
        component = CppComponent(
            id=f"{relative_path}::{class_name}::{cursor.spelling}::METHOD",
            component_type="method",
            file_path=file_path,
            relative_path=relative_path
        )
        self._set_component_details(component, cursor)
        self.components[component.id] = component

    def _add_member_variable(self, cursor, class_name: str, file_path: str, relative_path: str):
        """Add a class member variable component."""
        component = CppComponent(
            id=f"{relative_path}::{class_name}::{cursor.spelling}::MEMBER_VAR",
            component_type="member_variable",
            file_path=file_path,
            relative_path=relative_path
        )
        self._set_component_details(component, cursor)
        self.components[component.id] = component
        
    def _set_component_details(self, component: CppComponent, cursor):
        """Set common details for a component."""
        try:
            # Get accurate source locations
            start = cursor.extent.start
            end = cursor.extent.end
            
            # Convert to 1-based line numbers as used by the rest of the system
            component.start_line = start.line
            component.end_line = end.line
            
            # Get the exact source code
            component.source_code = self._get_source_between_locations(
                cursor.location.file.name,
                start.line,
                end.line
            )
            
            # Check for and extract docstring
            if has_docstring(component.source_code):
                component.has_docstring = True
                component.docstring = _extract_docstring(cursor) or ""
            else:
                component.has_docstring = False
                component.docstring = ""

        except Exception as e:
            logger.error(f"Error setting component details: {e}")

    def _get_source_between_locations(self, file_path: str, start_line: int, end_line: int) -> str:
        """Extract source code between two line numbers."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                # Convert to 0-based indexing
                return ''.join(lines[start_line-1:end_line])
        except Exception as e:
            logger.error(f"Error reading source code: {e}")
            return ""

    def _extract_component(self, cursor, file_path: str, relative_path: str) -> Optional[Dict[str, Any]]:
        """Extract component information from a clang cursor."""
        if not cursor.extent.start.file or cursor.extent.start.file.name != file_path:
            return None
            
        start_line = cursor.extent.start.line
        end_line = cursor.extent.end.line
        
        # Get the source code
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        source_code = ''.join(lines[start_line-1:end_line])
        
        # Get component type and ID
        if cursor.kind == CursorKind.CLASS_DECL:
            comp_type = "class"
            comp_id = f"{relative_path}::{cursor.spelling}::CLASS_DECL"
        elif cursor.kind == CursorKind.CXX_METHOD:
            comp_type = "method"
            parent = cursor.semantic_parent
            class_name = parent.spelling if parent else ""
            comp_id = f"{relative_path}::{class_name}::{cursor.spelling}::CXX_METHOD"
        elif cursor.kind == CursorKind.CONSTRUCTOR:
            comp_type = "method"
            parent = cursor.semantic_parent
            class_name = parent.spelling if parent else ""
            comp_id = f"{relative_path}::{class_name}::{cursor.spelling}::CONSTRUCTOR"
        elif cursor.kind == CursorKind.FUNCTION_DECL:
            comp_type = "function"
            comp_id = f"{relative_path}::{cursor.spelling}::FUNCTION_DECL"
        else:
            return None
            
        # Extract existing docstring if present
        docstring = None
        tokens = list(cursor.get_tokens())
        for i, token in enumerate(tokens):
            if token.kind == TokenKind.COMMENT:
                comment = token.spelling
                if comment.startswith('/**') and comment.endswith('*/'):
                    docstring = comment
                    
        return {
            "id": comp_id,
            "type": comp_type,
            "source_code": source_code,
            "start_line": start_line,
            "end_line": end_line,
            "docstring": docstring
        }

# Add this function to allow import from other modules
from src.dependency_analyzer.cpp_parser import CppParser

def parse_cpp_file(file_path: str, repo_path: str = None):
    """
    Parse a C++ file and return its components as a dictionary.
    This is a wrapper for CppParser for compatibility with code expecting a parse_cpp_file function.
    If repo_path is not provided, it will use the directory of the file_path.
    """
    if repo_path is None:
        repo_path = os.path.dirname(file_path)
    parser = CppParser(repo_path)
    return parser.parse_file(file_path)