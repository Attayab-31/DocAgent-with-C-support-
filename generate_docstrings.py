#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates
import warnings
warnings.filterwarnings("ignore", category=SyntaxWarning, message="invalid escape sequence")

"""
Docstring Generator with Dependency-Based Ordering

This script generates docstrings for Python code components (functions, classes, methods)
using a DFS-based approach that starts from components with no dependencies.

Key features:
1. Parses Python code to identify components and their dependencies
2. Builds a dependency graph where A→B means "A depends on B"
3. Performs DFS traversal starting from components with no dependencies
4. Processes dependencies before the components that depend on them
5. Ensures classes depend on their methods, not vice versa
6. Skips __init__ methods as they typically don't need separate docstrings
7. Provides visual representation of progress in the terminal

Usage:
    python generate_docstrings.py --repo-path PATH --config-path PATH [--test-mode]
"""

# Import necessary modules
import os
import sys
import time
import ast
import json
import argparse
import logging
import random
from pathlib import Path
from typing import Dict, List, Set, Optional, Any
from collections import defaultdict
import tiktoken  # Add this import for token counting

# Setup logging with reduced verbosity
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',  # Simplest format, just the message
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Configure specific loggers
cpp_logger = logging.getLogger('src.dependency_analyzer.cpp_parser')
cpp_logger.setLevel(logging.WARNING)  # Only show warnings and errors

# Silence third-party loggers
for logger_name in ['anthropic', 'urllib3', 'requests']:
    logging.getLogger(logger_name).setLevel(logging.ERROR)

logger = logging.getLogger("docstring_generator")

# Import dependency analyzer modules
from src.dependency_analyzer import (
    CodeComponent, 
    DependencyParser, 
    dependency_first_dfs, 
    build_graph_from_components
)
from src.visualizer import ProgressVisualizer
from src.agent.orchestrator import Orchestrator


def generate_test_docstring(component: CodeComponent) -> str:
    """
    Generate a placeholder docstring for test mode.
    
    Args:
        component: The code component to generate a placeholder docstring for.
        
    Returns:
        A placeholder docstring based on the component type.
    """
    comp_type = component.component_type
    name = component.id.split(".")[-1]
    
    if comp_type == "function":
        return f"""
        Test docstring for function '{name}'.
        
        This is a placeholder docstring generated in test mode.
        In normal mode, this would be replaced with an AI-generated docstring.
        
        Args:
            arg1: Description of first argument
            arg2: Description of second argument
            
        Returns:
            Description of return value
        """
    elif comp_type == "class":
        return f"""
        Test docstring for class '{name}'.
        
        This is a placeholder docstring generated in test mode.
        In normal mode, this would be replaced with an AI-generated docstring.
        
        Attributes:
            attr1: Description of first attribute
            attr2: Description of second attribute
        """
    elif comp_type == "method":
        class_name = component.id.split(".")[-2]
        return f"""
        Test docstring for method '{name}' in class '{class_name}'.
        
        This is a placeholder docstring generated in test mode.
        In normal mode, this would be replaced with an AI-generated docstring.
        
        Args:
            arg1: Description of first argument
            arg2: Description of second argument
            
        Returns:
            Description of return value
        """
    else:
        return f"""
        Test docstring for {comp_type} '{name}'.
        
        This is a placeholder docstring generated in test mode.
        """


def generate_docstring_for_component(component: CodeComponent, orchestrator: Optional[Orchestrator], test_mode: str = 'none',
                                     dependency_graph: Optional[Dict[str, List[str]]] = None) -> str:
    """
    Generate a docstring for a single component.
    
    Args:
        component: The component to generate a docstring for.
        orchestrator: The orchestrator instance.
        test_mode: The test mode to use.
        dependency_graph: Optional dependency graph.
        
    Returns:
        The generated docstring.
    """

    if not orchestrator:
        return ""
    
    file_path = component.file_path
    
    # Get the component code
    component_code = component.source_code
    
    # Estimate token count of the focal component
    encoding = tiktoken.get_encoding("cl100k_base")  # Default OpenAI encoding
    token_consume_focal = len(encoding.encode(component_code))
    
    # Skip if the component is too large (> 10000 tokens)
    if token_consume_focal > 10000:
        # truncate the component code to 10000 tokens
        component_code = encoding.decode(encoding.encode(component_code)[:10000])
    
    # Handle Python and C++ files differently
    if file_path.endswith(('.cpp', '.hpp', '.cc', '.h', '.cxx')):
        # For C++ files, we don't parse AST but use the source code directly
        try:
            # Pass component.id as the focal_node_dependency_path
            docstring = orchestrator.process(
                focal_component=component_code,
                file_path=file_path,
                ast_node=None,  # No AST for C++ files
                ast_tree=None,  # No AST for C++ files
                dependency_graph=dependency_graph,
                focal_node_dependency_path=component.id,
                token_consume_focal=token_consume_focal
            )
            return docstring
        except Exception as e:
            logger.error(f"Error generating docstring for {component.id}: {str(e)}")
            return ""
    
    # Handle Python files using ast parser
    try:
        # Parse the file
        with open(file_path, "r", encoding="utf-8") as f:
            file_content = f.read()
        
        ast_tree = ast.parse(file_content)
        ast_node = None
        
        # Locate the AST node for the component
        component_parts = component.id.split(".")
        component_name = component_parts[-1]
        
        if component.component_type == "function":
            # Find top-level function
            for node in ast.iter_child_nodes(ast_tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) 
                        and node.name == component_name):
                    ast_node = node
                    break
        elif component.component_type == "class":
            # Find class
            for node in ast.iter_child_nodes(ast_tree):
                if isinstance(node, ast.ClassDef) and node.name == component_name:
                    ast_node = node
                    break
                
        elif component.component_type == "method":
            # Find method inside class
            class_name, method_name = component_parts[-2:]
            for node in ast.iter_child_nodes(ast_tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for item in node.body:
                        if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) 
                                and item.name == method_name):
                            ast_node = item
                            break
                    break
        
        # Pass component.id as the focal_node_dependency_path
        docstring = orchestrator.process(
            focal_component=component_code,
            file_path=file_path,
            ast_node=ast_node,
            ast_tree=ast_tree,
            dependency_graph=dependency_graph,
            focal_node_dependency_path=component.id,
            token_consume_focal=token_consume_focal
        )
        return docstring
    except Exception as e:
        logger.error(f"Error generating docstring for {component.id}: {str(e)}")
        return ""


def set_docstring_in_file(file_path: str, component: CodeComponent, docstring: str) -> bool:
    """
    Update a file with a newly generated docstring for a component.
    Supports both Python and C++ files.
    
    Args:
        file_path: Path to the file to update.
        component: The component to update with a docstring.
        docstring: The docstring to insert.
        
    Returns:
        True if successful, False otherwise.
    """
    # Handle C++ files
    if file_path.endswith(('.cpp', '.hpp', '.h', '.cxx', '.cc')):
        try:
            from src.dependency_analyzer.cpp_parser import CLANG_AVAILABLE
            if not CLANG_AVAILABLE:
                logger.error("Cannot edit C++ files - clang not available. Please install LLVM and clang.")
                return False

            from src.dependency_analyzer.cpp_docstring import insert_cpp_docstring

            # Validate start_line
            if component.start_line <= 0:
                logger.error(f"Invalid start line for component {component.id}: {component.start_line}")
                return False

            # Insert the docstring
            success = insert_cpp_docstring(file_path, docstring, component.start_line)
            if not success:
                logger.error(f"Failed to insert docstring for component {component.id} in file {file_path}")
            return success

        except ImportError as e:
            logger.error(f"Error importing C++ modules: {e}")
            return False

    # Handle Python files
    # Read the file
    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()
    
    # Parse the file
    tree = ast.parse(source)
    
    # Find the component in the parsed AST
    component_node = None
    component_parts = component.id.split(".")
    component_name = component_parts[-1]
    
    if component.component_type == "function":
        # Find top-level function
        for node in ast.iter_child_nodes(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) 
                    and node.name == component_name):
                component_node = node
                break
                
    elif component.component_type == "class":
        # Find class
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef) and node.name == component_name:
                component_node = node
                break
                
    elif component.component_type == "method":
        # Find method inside class
        class_name, method_name = component_parts[-2:]
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) 
                            and item.name == method_name):
                        component_node = item
                        break
                break
    
    if not component_node:
        logger.error(f"Could not find component {component.id} in {file_path}")
        return False
    
    # Set the docstring
    set_node_docstring(component_node, docstring)
    
    # Unparse the AST back to source code
    if hasattr(ast, "unparse"):
        new_source = ast.unparse(tree)
    else:
        try:
            import astor
            new_source = astor.to_source(tree)
        except ImportError:
            logger.error(
                "Error: You need to install 'astor' or use Python 3.9+ to unparse the AST. "
                f"Skipping file: {file_path}"
            )
            return False
    
    # Write back to the file
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_source)
    
    return True


def set_node_docstring(node: ast.AST, docstring: str):
    """
    Safely set or update the docstring on an AST node (ClassDef, FunctionDef, etc.).
    Also adjusts indentation relative to the node's existing indentation level,
    ensuring both the opening and closing triple quotes are properly aligned.

    Args:
        node: The AST node to modify (ClassDef, FunctionDef, etc.).
        docstring: The new docstring (as a plain string) to insert.
    """
    import textwrap
    
    # 1. Strip leading/trailing empty lines in the provided docstring
    #    to avoid spurious blank lines.
    stripped_docstring = docstring.strip('\n')
    if not stripped_docstring:
        # If empty or all whitespace, provide a placeholder
        stripped_docstring = "No docstring provided."

    # 2. Dedent possible indentation in docstring (so a multiline docstring
    #    doesn't carry undesired left margins).
    dedented = textwrap.dedent(stripped_docstring)

    # 3. Determine how many spaces to indent for doc lines plus triple quotes.
    existing_indent = getattr(node, 'col_offset', 0)
    doc_indent_str = ' ' * (existing_indent + 4)

    # 4. Build the final string: 
    #    - Start with a newline (so triple quotes appear on a new line).
    #    - Indent all docstring lines.
    #    - End with a newline+same indentation (so the closing triple quotes
    #      line also has the doc_indent_str).
    prepared_docstring = (
        "\n"
        + textwrap.indent(dedented, doc_indent_str)
        + "\n"
        + doc_indent_str
    )

    # 5. Create an AST Expr node to store this docstring as a constant.
    docstring_node = ast.Expr(value=ast.Constant(value=prepared_docstring, kind=None))

    # If there's no body, just make one containing our new docstring.
    if not hasattr(node, "body") or not isinstance(node.body, list) or len(node.body) == 0:
        node.body = [docstring_node]
    else:
        # If the first statement is an existing docstring, replace it;
        # otherwise, insert the new docstring as the first statement.
        first_stmt = node.body[0]
        if (
            isinstance(first_stmt, ast.Expr)
            and isinstance(first_stmt.value, ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            node.body[0] = docstring_node
        else:
            node.body.insert(0, docstring_node)


def main():
    """
    Main entry point for the docstring generation script with flexible component ordering.
    
    The script supports different ordering modes through the --order-mode flag:
    - 'topo' (default): Dependency-based ordering using a DFS-based approach:
        1. If A depends on B, the graph has an edge A→B (meaning "A depends on B")
        2. Root nodes (nodes with no dependencies) are processed first
        3. Dependencies are always processed before the components that depend on them
        4. This ensures proper docstring generation order
    - 'random_node': Randomly shuffles all Python components, ignoring dependencies
    - 'random_file': Processes files in random order, but preserves component order within files
    
    Class methods are processed before the classes that depend on them (not vice versa) in 'topo' mode,
    ensuring proper docstring generation order. Special __init__ methods are skipped as
    they typically don't need separate docstrings.
    
    The script provides options to skip or overwrite existing docstrings:
    - By default, components with existing docstrings are skipped
    - With --overwrite-docstrings flag, existing docstrings will be overwritten
    - This behavior can also be configured in the config.yaml file under docstring_options.overwrite_docstrings
    
    Web interface integration:
    - With --enable-web flag, the script enables integration with the web UI
    - This allows visualization of the docstring generation process in a web browser
    - Run the web UI separately using the run_web_ui.py script
    """
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Generate docstrings for Python components in dependency order.'
    )
    parser.add_argument(
        '--repo-path', 
        type=str, 
        default='data/raw_test_repo',
        help='Path to the repository (default: data/raw_test_repo)'
    )
    parser.add_argument(
        '--config-path', 
        type=str, 
        default='config/agent_config.yaml',
        help='Path to the configuration file (default: config/agent_config.yaml)'
    )
    parser.add_argument(
        '--test-mode',
        type=str,
        choices=['placeholder', 'context_print', 'none'],
        default='none',
        help='Test mode to run: "placeholder" for placeholder docstrings (no LLM calls), "context_print" to print context before writer calls, "none" for normal operation'
    )
    parser.add_argument(
        '--order-mode',
        type=str,
        choices=['topo', 'random_node', 'random_file'],
        default='topo',
        help='Order mode for docstring generation: "topo" follows dependency order (default), "random_node" selects random Python nodes, "random_file" processes files in random order'
    )
    parser.add_argument(
        '--enable-web',
        action='store_true',
        help='Enable integration with the web interface'
    )
    parser.add_argument(
        '--overwrite-docstrings',
        action='store_true',
        help='Overwrite existing docstrings instead of skipping them (default: False)'
    )
    parser.add_argument(
        '--parallel',
        action='store_true',
        help='Enable parallel processing of independent components'
    )
    parser.add_argument(
        '--max-workers',
        type=int,
        default=None,
        help='Maximum number of parallel workers (defaults to CPU count)'
    )
    
    args = parser.parse_args()
    repo_path = args.repo_path
    config_path = args.config_path
    test_mode = args.test_mode
    order_mode = args.order_mode
    overwrite_docstrings = args.overwrite_docstrings
    
    # Create output directory for dependency graph
    output_dir = os.path.join("output", "dependency_graphs")
    os.makedirs(output_dir, exist_ok=True)
    
    # Extract repository name from path for creating a unique filename
    repo_name = os.path.basename(os.path.normpath(repo_path))
    # Create a sanitized version of the repo name (remove special characters)
    sanitized_repo_name = ''.join(c if c.isalnum() else '_' for c in repo_name)
    dependency_graph_path = os.path.join(output_dir, f"{sanitized_repo_name}_dependency_graph.json")
    
    # Initialize orchestrator
    if test_mode != 'placeholder':
        orchestrator = Orchestrator(repo_path=repo_path, config_path=config_path, 
                                  test_mode=test_mode if test_mode != 'none' else None)
        
        # Check config overwrite setting
        if hasattr(orchestrator, 'config'):
            config_overwrite = orchestrator.config.get('docstring_options', {}).get('overwrite_docstrings')
            if config_overwrite is not None:
                overwrite_docstrings = config_overwrite

    # Parse repository and build graph
    parser = DependencyParser(repo_path)
    components = parser.parse_repository()
    parser.save_dependency_graph(dependency_graph_path)
    
    # Build dependency graph and sort components
    graph = build_graph_from_components(components)
    sorted_components = dependency_first_dfs(graph)
    dependency_graph = {component_id: list(deps) for component_id, deps in graph.items()}

    print("\nDocstring Generator")
    print("=" * 50)
    print(f"Repository: {repo_path}")
    print(f"Components found: {len(sorted_components)}")
    print(f"Mode: {test_mode if test_mode != 'none' else 'normal'}")
    print("=" * 50)
    
    # Apply the selected ordering mode
    if order_mode == 'random_node':
        # Randomly shuffle all components
        logger.info("Using random node ordering mode - shuffling all components")
        random.shuffle(sorted_components)
    elif order_mode == 'random_file':
        # Group components by file path
        logger.info("Using random file ordering mode - processing files in random order")
        # Group components by file
        file_to_components = defaultdict(list)
        for component_id in sorted_components:
            component = components.get(component_id)
            if component:
                file_to_components[component.file_path].append(component_id)
        
        # Randomly shuffle the file order but maintain the order of components within each file
        file_paths = list(file_to_components.keys())
        random.shuffle(file_paths)
        
        # Create a new ordering based on randomly shuffled files
        sorted_components = []
        for file_path in file_paths:
            sorted_components.extend(file_to_components[file_path])

    # Initialize visualization
    visualizer = ProgressVisualizer(components, sorted_components)
    visualizer.initialize()

    print("\nDocstring Generator Progress")
    print("=" * 30)
    print(f"Total components: {len(sorted_components)}")
    print(f"Mode: {order_mode}")
    print("=" * 30)
    print()
    
    # Check if web interface is enabled
    if args.enable_web:
        try:
            from src.visualizer.web_bridge import patch_visualizers
            logger.info("Web interface integration enabled")
            patch_visualizers()
        except ImportError as e:
            logger.warning(f"Failed to enable web interface integration: {e}")
            logger.warning("Make sure you have installed the required web dependencies")

    # Initialize the progress visualizer
    visualizer = ProgressVisualizer(components, sorted_components)
    visualizer.initialize()
    
    # Show dependency statistics
    visualizer.show_dependency_stats()
    
    # Initialize parallel processor if enabled
    parallel_processor = None
    if args.parallel:
        from src.agent.parallel_processor import ParallelProcessor
        
        parallel_processor = ParallelProcessor(
            repo_path=repo_path,
            config_path=config_path,
            max_workers=args.max_workers
        )
        
        # Get dependency levels for parallel processing
        dependency_levels = parallel_processor.get_dependency_levels(dependency_graph)
        logger.info(f"Components grouped into {len(dependency_levels)} dependency levels")
        
        # Process each level in parallel
        ast_trees = {}
        for comp_id in sorted_components:
            comp = components[comp_id]
            if comp.file_path not in ast_trees:
                try:
                    with open(comp.file_path, 'r', encoding='utf-8') as f:
                        # Check file type and parse accordingly
                        if comp.file_path.endswith('.cpp') or comp.file_path.endswith('.hpp'):
                            from src.dependency_analyzer.cpp_parser import parse_cpp_file
                            ast_trees[comp.file_path] = parse_cpp_file(comp.file_path)
                        else:
                            # Default to Python parsing
                            ast_trees[comp.file_path] = ast.parse(f.read())
                except Exception as e:
                    logger.error(f"Error parsing file {comp.file_path}: {str(e)}")
                    ast_trees[comp.file_path] = None  # Mark as failed to parse
        
        for level_idx, level_components in enumerate(dependency_levels):
            logger.info(f"Processing level {level_idx + 1}/{len(dependency_levels)} with {len(level_components)} components")
            results = parallel_processor.process_level(components, ast_trees, dependency_graph, level_components)
            
            # Update docstrings for this level
            for comp_id, docstring in results.items():
                if docstring:
                    component = components[comp_id]
                    success = set_docstring_in_file(component.file_path, component, docstring)
                    visualizer.update(comp_id, "completed" if success else "error")
    else:
        # Process components sequentially
        for component_id in sorted_components:
            component = components.get(component_id)
            if not component:
                continue

            # Skip __init__ methods
            if component.component_type == "method" and component_id.endswith(".__init__"):
                visualizer.update(component_id, "completed")
                continue

            # Check if we should skip this component
            docstring_length = len(component.docstring.split()) if component.has_docstring else 0
            if component.has_docstring and not overwrite_docstrings and docstring_length > 10:
                visualizer.update(component_id, "completed")
                continue

            # Generate docstring
            visualizer.update(component_id, "processing")
            print(f"\nProcessing: {component_id}")
            
            docstring = generate_docstring_for_component(component, orchestrator, test_mode, dependency_graph)
            success = set_docstring_in_file(component.file_path, component, docstring)
            
            visualizer.update(component_id, "completed" if success else "error")
            if not success:
                logger.error(f"Failed to generate docstring for {component_id}")
            
            # Re-parse if needed for line number updates
            if [c for c in sorted_components if c != component_id and components[c].file_path == component.file_path]:
                parser = DependencyParser(repo_path)
                components.update(parser.parse_repository())
    
    # Finalize visualization
    visualizer.finalize()
    
    # Print completion summary
    print("\nGeneration Complete")
    print("=" * 50)

    # Show stats
    by_type = defaultdict(int)
    for component_id in sorted_components:
        if component := components.get(component_id):
            by_type[component.component_type] += 1

    print("\nComponents processed:")
    for type_name, count in by_type.items():
        print(f"  {type_name.title()}: {count}")

    # Print cost if available
    if orchestrator:
        try:
            rate_limiters = [
                agent.llm.rate_limiter for agent_name in ['reader', 'writer', 'verifier']
                if (agent := getattr(orchestrator, agent_name, None))
                and hasattr(agent, 'llm') and hasattr(agent.llm, 'rate_limiter')
            ]
            
            if rate_limiters:
                total_cost = sum(limiter.total_cost for limiter in rate_limiters)
                print(f"\nTotal cost: ${total_cost:.4f}")
        except Exception:
            pass


if __name__ == "__main__":
    main()