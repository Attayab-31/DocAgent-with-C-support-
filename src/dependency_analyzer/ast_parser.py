# Copyright (c) Meta Platforms, Inc. and affiliates
"""
AST-based code parser that extracts dependency information between code components.
Supports both Python and C++ code components.
"""

import ast
import os
import json
import logging
import builtins
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Any, Union, TypeVar
from pathlib import Path

try:
    from .cpp_parser import CppParser, CLANG_AVAILABLE
except ImportError:
    CppParser = None
    CLANG_AVAILABLE = False

logger = logging.getLogger(__name__)

# Built-in Python types and modules that should be excluded from dependencies
BUILTIN_TYPES = {name for name in dir(builtins)}
STANDARD_MODULES = {'abc', 'argparse', 'array', 'asyncio', 'base64', 'collections', 'copy', 
    'csv', 'datetime', 'enum', 'functools', 'glob', 'io', 'itertools', 
    'json', 'logging', 'math', 'os', 'pathlib', 'random', 're', 'shutil', 
    'string', 'sys', 'time', 'typing', 'uuid', 'warnings', 'xml'
}
EXCLUDED_NAMES = {'self', 'cls'}

@dataclass
class CodeComponent:
    """Base class for code components (functions, classes, methods)."""
    id: str
    component_type: str  # "function", "class", or "method"
    file_path: str
    relative_path: str
    source_code: str = ""
    depends_on: Set[str] = field(default_factory=set)
    start_line: int = 0
    end_line: int = 0
    has_docstring: bool = False
    docstring: str = ""
    language: str = "python"  # Can be "python" or "cpp"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "component_type": self.component_type,
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "depends_on": list(self.depends_on),
            "start_line": self.start_line,
            "end_line": self.end_line,
            "has_docstring": self.has_docstring,
            "docstring": self.docstring,
            "language": self.language,
            "source_code": self.source_code
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CodeComponent':
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
        component.language = data.get('language', 'python')
        component.source_code = data.get('source_code', '')
        return component


class ImportCollector(ast.NodeVisitor):
    """Collects import statements from Python code."""
    
    def __init__(self):
        self.imports = set()
        self.from_imports = {}  # module -> [names]
        
    def visit_Import(self, node: ast.Import):
        """Process 'import x' statements."""
        for name in node.names:
            self.imports.add(name.name)
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Process 'from x import y' statements."""
        if node.module is not None:
            module = node.module
            if module not in self.from_imports:
                self.from_imports[module] = []
            
            for name in node.names:
                if name.name != '*':
                    self.from_imports[module].append(name.name)
        
        self.generic_visit(node)


class MethodDependencyCollector(ast.NodeVisitor):
    """
    Special dependency collector for methods that also tracks 'self.XXX' references
    as potential dependencies.
    """
    
    def __init__(self, class_id: str, method_id: str, class_methods: Dict[str, str]):
        self.class_id = class_id
        self.method_id = method_id
        self.class_methods = class_methods  # method_name -> full_method_id
        self.self_attr_refs = set()  # Set of attributes accessed via self.XXX
        
    def visit_Attribute(self, node: ast.Attribute):
        """Process attribute access, specifically looking for self.XXX references."""
        if (isinstance(node.value, ast.Name) and 
            node.value.id == 'self' and 
            isinstance(node.ctx, ast.Load)):
            
            # Found a self.XXX reference
            attr_name = node.attr
            self.self_attr_refs.add(attr_name)
        
        self.generic_visit(node)
    
    def get_method_dependencies(self) -> Set[str]:
        """
        Get the set of methods that this method depends on based on self.XXX references.
        
        Returns:
            A set of method IDs that this method depends on
        """
        dependencies = set()
        
        # Check if any self.attr references match method names
        for attr in self.self_attr_refs:
            if attr in self.class_methods:
                # This is a reference to another method in the class
                dependencies.add(self.class_methods[attr])
        
        return dependencies


class DependencyCollector(ast.NodeVisitor):
    """
    Collects dependencies between code components by analyzing
    attribute access, function calls, and class references.
    """
    
    def __init__(self, imports, from_imports, current_module, repo_modules):
        self.imports = imports
        self.from_imports = from_imports
        self.current_module = current_module
        self.repo_modules = repo_modules
        self.dependencies = set()
        self._current_class = None
        # Track local variables defined in the current context
        self.local_variables = set()
    
    def visit_ClassDef(self, node: ast.ClassDef):
        """Process class definitions."""
        old_class = self._current_class
        self._current_class = node.name
        
        # Check for base classes dependencies
        for base in node.bases:
            if isinstance(base, ast.Name):
                # Simple name reference, could be an imported class
                self._add_dependency(base.id)
            elif isinstance(base, ast.Attribute):
                # Module.Class reference
                self._process_attribute(base)
        
        self.generic_visit(node)
        self._current_class = old_class
    
    def visit_Assign(self, node: ast.Assign):
        """Track local variable assignments."""
        for target in node.targets:
            if isinstance(target, ast.Name):
                # Add to local variables
                self.local_variables.add(target.id)
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call):
        """Process function calls."""
        if isinstance(node.func, ast.Name):
            # Direct function call
            self._add_dependency(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            # Method call or module.function call
            self._process_attribute(node.func)
        
        self.generic_visit(node)
    
    def visit_Name(self, node: ast.Name):
        """Process name references."""
        if isinstance(node.ctx, ast.Load):
            self._add_dependency(node.id)
        self.generic_visit(node)
    
    def visit_Attribute(self, node: ast.Attribute):
        """Process attribute access."""
        self._process_attribute(node)
        self.generic_visit(node)
    
    def _process_attribute(self, node: ast.Attribute):
        """Process an attribute node to extract potential dependencies."""
        parts = []
        current = node
        
        # Traverse the attribute chain (e.g., module.submodule.Class.method)
        while isinstance(current, ast.Attribute):
            parts.insert(0, current.attr)
            current = current.value
        
        if isinstance(current, ast.Name):
            parts.insert(0, current.id)
            
            # Skip if the first part is a local variable
            if parts[0] in self.local_variables:
                return
                
            # Skip if the first part is in our excluded names
            if parts[0] in EXCLUDED_NAMES:
                return
                
            # Check if the first part is an imported module
            if parts[0] in self.imports:
                module_path = parts[0]
                # Skip standard library modules
                if module_path in STANDARD_MODULES:
                    return
                    
                # If it's a repo module, add as dependency
                if module_path in self.repo_modules:
                    if len(parts) > 1:
                        # Example: module.Class or module.function
                        self.dependencies.add(f"{module_path}.{parts[1]}")
            
            # Check from imports
            elif parts[0] in self.from_imports.keys():
                # Skip standard library modules
                if parts[0] in STANDARD_MODULES:
                    return
                    
                # Check if the name is in the imported names
                if len(parts) > 1 and parts[1] in self.from_imports[parts[0]]:
                    self.dependencies.add(f"{parts[0]}.{parts[1]}")
    
    def _add_dependency(self, name):
        """Add a potential dependency based on a name reference."""
        # Skip built-in types
        if name in BUILTIN_TYPES:
            return
            
        # Skip excluded names
        if name in EXCLUDED_NAMES:
            return
            
        # Skip local variables
        if name in self.local_variables:
            return
            
        # Check if name is directly imported from a module
        for module, imported_names in self.from_imports.items():
            # Skip standard library modules
            if module in STANDARD_MODULES:
                continue
                
            if name in imported_names and module in self.repo_modules:
                self.dependencies.add(f"{module}.{name}")
                return
                
        # Check if name refers to a component in the current module
        local_component_id = f"{self.current_module}.{name}"
        self.dependencies.add(local_component_id)


def add_parent_to_nodes(tree: ast.AST) -> None:
    """
    Add a 'parent' attribute to each node in the AST.
    
    Args:
        tree: The AST to process
    """
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node


class DependencyParser:
    """Parses code to build a dependency graph between components."""
    
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.components: Dict[str, CodeComponent] = {}
        self.cpp_parser = CppParser(repo_path) if CLANG_AVAILABLE else None
        
    def parse_file(self, file_path: str) -> List[CodeComponent]:
        """Parse a file and extract code components with their dependencies."""
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return []
            
        relative_path = os.path.relpath(file_path, self.repo_path)
        module_path = os.path.splitext(relative_path)[0].replace(os.sep, '.')
        
        # Handle C++ files
        if file_path.endswith(('.cpp', '.hpp', '.h', '.cc', '.cxx')):
            if not CLANG_AVAILABLE:
                logger.error("Clang not available - skipping C++ file")
                return []
                
            try:
                components = []
                parsed_components = self.cpp_parser.parse_file(file_path, self.repo_path)
                for comp in parsed_components:
                    component = CodeComponent(
                        id=comp['id'],
                        component_type=comp['type'],
                        file_path=file_path,
                        relative_path=relative_path,
                        source_code=comp['source_code'],
                        start_line=comp['start_line'],
                        end_line=comp['end_line'],
                        language="cpp"
                    )
                    if 'docstring' in comp:
                        component.has_docstring = True
                        component.docstring = comp['docstring']
                    components.append(component)
                    self.components[component.id] = component
                return components
                
            except Exception as e:
                logger.error(f"Error parsing C++ file {file_path}: {str(e)}")
                return []
                
        # Handle Python files
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
            return self._collect_components(tree, file_path, relative_path, module_path, source)
        except Exception as e:
            logger.error(f"Error parsing Python file {file_path}: {str(e)}")
            return []
    
    def _collect_components(self, tree: ast.AST, file_path: str, relative_path: str, module_path: str, source: str) -> List[CodeComponent]:
        """Collect code components from an AST."""
        components = []
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip __init__ methods as they typically don't need separate docs
                if node.name == '__init__':
                    continue
                    
                # Get the source code lines
                start_line = node.lineno
                end_line = node.end_lineno if hasattr(node, 'end_lineno') else start_line
                source_lines = source.splitlines()[start_line-1:end_line]
                component_source = '\n'.join(source_lines)
                
                # Check if it's a method or standalone function
                parent_class = None
                for parent in ast.walk(tree):
                    if isinstance(parent, ast.ClassDef) and node in parent.body:
                        parent_class = parent
                        break
                
                if parent_class:
                    # It's a method
                    comp_id = f"{module_path}.{parent_class.name}.{node.name}"
                    comp_type = "method"
                else:
                    # It's a function
                    comp_id = f"{module_path}.{node.name}"
                    comp_type = "function"
                    
                component = CodeComponent(
                    id=comp_id,
                    component_type=comp_type,
                    file_path=file_path,
                    relative_path=relative_path,
                    source_code=component_source,
                    start_line=start_line,
                    end_line=end_line
                )
                
                # Check for existing docstring
                docstring = ast.get_docstring(node)
                if docstring:
                    component.has_docstring = True
                    component.docstring = docstring
                    
                components.append(component)
                
            elif isinstance(node, ast.ClassDef):
                # Get the source code lines
                start_line = node.lineno
                end_line = node.end_lineno if hasattr(node, 'end_lineno') else start_line
                source_lines = source.splitlines()[start_line-1:end_line]
                component_source = '\n'.join(source_lines)
                
                comp_id = f"{module_path}.{node.name}"
                component = CodeComponent(
                    id=comp_id,
                    component_type="class",
                    file_path=file_path,
                    relative_path=relative_path,
                    source_code=component_source,
                    start_line=start_line,
                    end_line=end_line
                )
                
                # Check for existing docstring
                docstring = ast.get_docstring(node)
                if docstring:
                    component.has_docstring = True
                    component.docstring = docstring
                    
                components.append(component)
                
        return components
    
    def _resolve_dependencies(self):
        """
        Second pass to resolve dependencies between components.
        Only processes Python components, C++ dependencies are handled separately by CppParser.
        """
        for component_id, component in self.components.items():
            file_path = component.file_path
            
            # Skip if the file is not a Python file
            if not file_path.endswith('.py'):
                continue
                
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    source = f.read()
                
                # Parse file to get imports
                tree = ast.parse(source)
                
                # Add parent field to AST nodes for easier traversal
                add_parent_to_nodes(tree)
                
                # Collect imports
                import_collector = ImportCollector()
                import_collector.visit(tree)
                
                # Find the component node in the tree
                component_node = None
                module_path = self._file_to_module_path(component.relative_path)
                
                if component.component_type == "function":
                    # Find top-level function
                    for node in ast.iter_child_nodes(tree):
                        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) 
                                and node.name == component.id.split(".")[-1]):
                            component_node = node
                            break
                
                elif component.component_type == "class":
                    # Find class
                    for node in ast.iter_child_nodes(tree):
                        if isinstance(node, ast.ClassDef) and node.name == component.id.split(".")[-1]:
                            component_node = node
                            break
                
                elif component.component_type == "method":
                    # Find method inside class
                    class_name, method_name = component.id.split(".")[-2:]
                    class_node = None
                    
                    for node in ast.iter_child_nodes(tree):
                        if isinstance(node, ast.ClassDef) and node.name == class_name:
                            class_node = node
                            for item in node.body:
                                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) 
                                        and item.name == method_name):
                                    component_node = item
                                    break
                            break
                
                if component_node:
                    # Collect dependencies for this specific component
                    dependency_collector = DependencyCollector(
                        import_collector.imports,
                        import_collector.from_imports,
                        module_path,
                        self.modules
                    )
                    
                    # For functions and methods, collect variables defined in the function
                    if isinstance(component_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Add function parameters to local variables
                        for arg in component_node.args.args:
                            dependency_collector.local_variables.add(arg.arg)
                            
                    dependency_collector.visit(component_node)
                    
                    # Add dependencies to the component
                    component.depends_on.update(dependency_collector.dependencies)
                    
                    # Filter out non-existent dependencies
                    component.depends_on = {
                        dep for dep in component.depends_on 
                        if dep in self.components or dep.split(".", 1)[0] in self.modules
                    }
                
            except (SyntaxError, UnicodeDecodeError) as e:
                logger.warning(f"Error analyzing dependencies in {file_path}: {e}")
    
    def _add_class_method_dependencies(self):
        """
        Third pass to make classes dependent on their methods (except __init__).
        """
        # Group components by class
        class_methods = {}
        
        # Collect all methods for each class
        for component_id, component in self.components.items():
            if component.component_type == "method":
                parts = component_id.split(".")
                if len(parts) >= 2:
                    method_name = parts[-1]
                    class_id = ".".join(parts[:-1])
                    
                    if class_id not in class_methods:
                        class_methods[class_id] = []
                    
                    # Don't include __init__ methods as dependencies of the class
                    if method_name != "__init__":
                        class_methods[class_id].append(component_id)
        
        # Add method dependencies to their classes
        for class_id, method_ids in class_methods.items():
            if class_id in self.components:
                class_component = self.components[class_id]
                for method_id in method_ids:
                    class_component.depends_on.add(method_id)
    
    def _get_source_segment(self, source: str, node: ast.AST) -> str:
        """Get source code segment for an AST node."""
        try:
            if hasattr(ast, "get_source_segment"):
                segment = ast.get_source_segment(source, node)
                if segment is not None:
                    return segment
            
            # Fallback to manual extraction
            lines = source.split("\n")
            start_line = node.lineno - 1
            end_line = getattr(node, "end_lineno", node.lineno) - 1
            return "\n".join(lines[start_line:end_line + 1])
        
        except Exception as e:
            logger.warning(f"Error getting source segment: {e}")
            return ""
    
    def _get_docstring(self, source: str, node: ast.AST) -> str:
        """Get the docstring for a given AST node."""
        try:
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                for item in node.body:
                    if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                        if isinstance(item.value.value, str):
                            return item.value.value
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                        if isinstance(item.value.value, str):
                            return item.value.value
            return ""
        except Exception as e:
            logger.warning(f"Error getting docstring: {e}")
            return ""
    
    def _file_to_module_path(self, relative_path: str) -> str:
        """
        Convert a file path to a Python module path.
        For example: 'src/module/file.py' -> 'src.module.file'
        """
        # Remove file extension
        module_path = os.path.splitext(relative_path)[0]
        # Replace path separators with dots
        module_path = module_path.replace('/', '.').replace('\\', '.')
        return module_path

    def save_dependency_graph(self, output_path: str):
        """
        Save the dependency graph to a JSON file.
        
        Handles both Python CodeComponent and C++ CppComponent objects.
        """
        # Convert to serializable format
        serializable_components = {}
        python_count = 0
        cpp_count = 0
        skipped_count = 0
        
        for comp_id, component in self.components.items():
            try:
                # Use duck typing to handle different component types
                if hasattr(component, 'to_dict') and callable(component.to_dict):
                    component_dict = component.to_dict()
                    
                    # Determine language from file extension or component type
                    if hasattr(component, 'file_path'):
                        file_ext = os.path.splitext(component.file_path)[1].lower()
                        if file_ext in ('.cpp', '.cc', '.cxx', '.hpp', '.hh', '.h'):
                            component_dict['language'] = 'cpp'
                            cpp_count += 1
                        elif file_ext == '.py':
                            component_dict['language'] = 'python'
                            python_count += 1
                    else:
                        # Fallback to class name check
                        if component.__class__.__name__ == 'CppComponent':
                            component_dict['language'] = 'cpp'
                            cpp_count += 1
                        else:
                            component_dict['language'] = 'python'
                            python_count += 1
                    
                    serializable_components[comp_id] = component_dict
                else:
                    logger.warning(f"Component {comp_id} doesn't have to_dict method, skipping")
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Error serializing component {comp_id}: {e}")
                skipped_count += 1
        
        # Create directories if they don't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(serializable_components, f, indent=2)
        
        total = python_count + cpp_count
        logger.info(
            f"Saved dependency graph to {output_path}\n"
            f"Components saved:\n"
            f"  - {python_count} Python components\n"
            f"  - {cpp_count} C++ components\n"
            f"  - {total} total components\n"
            f"  - {skipped_count} components skipped"
        )
    
    def load_dependency_graph(self, input_path: str):
        """
        Load the dependency graph from a JSON file.
        
        Handles both Python and C++ components.
        """
        with open(input_path, "r", encoding="utf-8") as f:
            serialized_components = json.load(f)
        
        self.components = {}
        python_count = 0
        cpp_count = 0
        
        for comp_id, comp_data in serialized_components.items():
            try:
                # Check the language field to determine the component type
                language = comp_data.get('language', 'python')  # Default to Python for backward compatibility
                
                if language == 'python':
                    self.components[comp_id] = CodeComponent.from_dict(comp_data)
                    python_count += 1
                elif language == 'cpp':
                    # Import CppComponent for deserialization
                    try:
                        from .cpp_parser import CppComponent
                        self.components[comp_id] = CppComponent.from_dict(comp_data)
                        cpp_count += 1
                    except ImportError:
                        logger.warning(f"Cannot load C++ component {comp_id}: C++ parser not available")
                else:
                    logger.warning(f"Unknown language {language} for component {comp_id}")
            except Exception as e:
                logger.error(f"Error deserializing component {comp_id}: {e}")
        
        logger.info(f"Loaded {python_count} Python components and {cpp_count} C++ components from {input_path}")
        return self.components
    
    def _resolve_all_dependencies(self):
        """
        Resolve dependencies for all components, including both Python and C++.
        """
        # First, resolve Python dependencies
        self._resolve_dependencies()
        
        # Now resolve C++ dependencies if available
        if self.cpp_parser:
            for component_id, component in self.components.items():
                if hasattr(component, 'component_type'):
                    if component.component_type in ('function', 'class', 'method'):
                        # Skip Python components
                        continue
                    
                    # This is a C++ component, resolve its dependencies
                    if hasattr(self.cpp_parser, '_resolve_component_dependencies'):
                        try:
                            self.cpp_parser._resolve_component_dependencies(component)
                        except Exception as e:
                            logger.error(f"Error resolving dependencies for C++ component {component_id}: {e}")
                            
        # After resolving individual component dependencies, analyze cross-language dependencies
        self._resolve_cross_language_dependencies()
    
    def _resolve_cross_language_dependencies(self):
        """
        Analyze and establish dependencies between Python and C++ components.
        This is a placeholder for more sophisticated cross-language analysis.
        """
        # This would implement a more sophisticated analysis of cross-language dependencies
        # For example, Python code calling C++ extensions or via ctypes/cffi
        logger.info("Cross-language dependency analysis not yet implemented")
    
    def parse_repository(self) -> Dict[str, CodeComponent]:
        """Parse all Python and C++ files in the repository."""
        for root, _, files in os.walk(self.repo_path):
            for file in files:
                if file.endswith(('.py', '.cpp', '.hpp', '.h', '.cc', '.cxx')):
                    file_path = os.path.join(root, file)
                    try:
                        components = self.parse_file(file_path)
                        for component in components:
                            self.components[component.id] = component
                    except Exception as e:
                        logger.error(f"Error parsing {file_path}: {str(e)}")
                        continue
        return self.components