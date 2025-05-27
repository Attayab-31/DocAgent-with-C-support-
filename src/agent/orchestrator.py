# Copyright (c) Meta Platforms, Inc. and affiliates
from typing import Dict, Any, Optional, List
import time
from .base import BaseAgent
from .reader import Reader
from .searcher import Searcher
from .writer import Writer
from .cpp_writer import CppWriter
from .verifier import Verifier
from ..visualizer import StatusVisualizer
import re
import yaml
import ast
import tiktoken

# Dummy visualizer class that mimics StatusVisualizer but does nothing
class DummyVisualizer:
    """A no-op visualizer that implements the same interface as StatusVisualizer but does nothing."""
    
    def reset(self):
        """Do nothing."""
        pass
    
    def set_current_component(self, component, file_path):
        """Do nothing."""
        pass
    
    def update(self, agent_name, status):
        """Do nothing."""
        pass

class Orchestrator(BaseAgent):
    """Agent responsible for managing the workflow between all other agents."""
    
    def __init__(self, repo_path: str, config_path: Optional[str] = None, test_mode: Optional[str] = None):
        super().__init__(config_path)
        self.repo_path = repo_path
        self.test_mode = test_mode

        # Initialize context caches
        self._file_context_cache = {}
        self._class_context_cache = {}
        self._method_context_cache = {}
        self._function_context_cache = {}
        self._current_class = None
        self._current_file = None
        
        # Load configuration
        self.config = {}
        if config_path:
            with open(config_path, 'r') as f:
                self.config = yaml.safe_load(f)
        
        # Get flow control parameters with defaults
        flow_config = self.config.get('flow_control', {})
        self.max_reader_search_attempts = flow_config.get('max_reader_search_attempts', 4)
        self.max_verifier_rejections = flow_config.get('max_verifier_rejections', 3)
        self.status_sleep_time = flow_config.get('status_sleep_time', 3)
        
        # Check model type for context constraints
        llm_config = self.config.get('llm', {})
        self.model_type = llm_config.get('type', 'openai')
        
        # Add max_input_tokens to config for context length constraint
        if 'max_input_tokens' not in self.config:
            self.config['max_input_tokens'] = llm_config.get('max_input_tokens', 10000)
        
        # Initialize visualization - use dummy visualizer for "context_print" test mode
        if test_mode == "context_print":
            self.visualizer = DummyVisualizer()
        else:
            self.visualizer = StatusVisualizer()
        
        # Initialize all sub-agents
        self.reader = Reader(config_path=config_path)
        self.searcher = Searcher(repo_path, config_path=config_path)
        
        # Only initialize writer and verifier if not in reader_searcher test mode
        if test_mode != "reader_searcher":
            # Initialize both Python and C++ writers
            self.py_writer = Writer(config_path=config_path)
            self.cpp_writer = CppWriter(config_path=config_path)
            self.verifier = Verifier(config_path=config_path)
            
        # Cache for contexts
        self.context = ""

    def _parse_verifier_response(self, response: str) -> Dict[str, Any]:
        """Parse the verifier's XML response into a structured format.
        
        Args:
            response: The XML response from the verifier
            
        Returns:
            Dictionary containing parsed verification results with structure:
            {
                'needs_revision': bool,
                'needs_context': bool,
                'suggestion': str,
                'context_suggestion': str
            }
        """
        result = {
            'needs_revision': False,
            'needs_context': False,
            'suggestion': '',
            'context_suggestion': ''
        }
        
        if not response:
            return result

        try:
            # Safely parse NEED_REVISION with default to False
            need_revision_match = re.search(r'<NEED_REVISION>(.*?)</NEED_REVISION>', response, re.DOTALL)
            result['needs_revision'] = (need_revision_match and 
                                    need_revision_match.group(1) and 
                                    need_revision_match.group(1).strip().lower() == 'true')
            
            if result['needs_revision']:
                # Safely parse MORE_CONTEXT with default to False
                more_context_match = re.search(r'<MORE_CONTEXT>(.*?)</MORE_CONTEXT>', response, re.DOTALL)
                result['needs_context'] = (more_context_match and 
                                        more_context_match.group(1) and 
                                        more_context_match.group(1).strip().lower() == 'true')
                
                if result['needs_context']:
                    # Extract context suggestion with fallback
                    context_suggestion_match = re.search(r'<SUGGESTION_CONTEXT>(.*?)</SUGGESTION_CONTEXT>', response, re.DOTALL)
                    result['context_suggestion'] = (context_suggestion_match.group(1).strip() 
                                                if context_suggestion_match and context_suggestion_match.group(1) 
                                                else "Additional context needed to improve docstring quality.")
                else:
                    # Extract improvement suggestion with fallback
                    suggestion_match = re.search(r'<SUGGESTION>(.*?)</SUGGESTION>', response, re.DOTALL)
                    result['suggestion'] = (suggestion_match.group(1).strip()
                                        if suggestion_match and suggestion_match.group(1)
                                        else "Please improve the docstring following standard documentation guidelines.")
        except Exception as e:
            # Handle any parsing errors gracefully
            result['needs_revision'] = True
            result['needs_context'] = False
            result['suggestion'] = f"Error parsing verification response: {str(e)}. Please review the docstring manually."
            
        return result

    def _cache_class_context(self, class_name: str, ast_node: ast.AST, ast_tree: ast.AST, dependency_graph: Dict[str, List[str]]) -> None:
        """Cache context for a class to be reused by its methods.
        
        Args:
            class_name: The name of the class
            ast_node: The AST node for the class
            ast_tree: The complete AST tree
            dependency_graph: The dependency graph
        """
        if class_name not in self._class_context_cache:
            # Get class-level context using searcher
            class_context = self.searcher.process(
                f"<REQUEST><INTERNAL><CALLS><CLASS>{class_name}</CLASS></CALLS><CALL_BY>true</CALL_BY></INTERNAL></REQUEST>",
                ast_node,
                ast_tree,
                dependency_graph,
                class_name
            )
            self._class_context_cache[class_name] = class_context

    def _get_cached_context(self, class_name: str, method_specific_context: Dict[str, Any]) -> Dict[str, Any]:
        """Get combined context from cache and method-specific info.
        
        Args:
            class_name: The name of the class
            method_specific_context: Context specific to the method
            
        Returns:
            Combined context dictionary
        """
        if class_name in self._class_context_cache:
            # Combine class context with method-specific context
            combined = dict(self._class_context_cache[class_name])
            
            # Only update with method-specific internal info
            if 'internal' in method_specific_context and 'internal' in combined:
                # Preserve class-level info while adding method-specific info
                combined['internal'].update(method_specific_context['internal'])
            
            # Always use method-specific external info if present
            if 'external' in method_specific_context:
                combined['external'] = method_specific_context['external']
                
            return combined
            
        return method_specific_context

    def process(
        self,
        focal_component: str,
        file_path: str,
        ast_node: ast.AST = None,
        ast_tree: ast.AST = None,
        dependency_graph: Dict[str, List[str]] = None,
        focal_node_dependency_path: str = None,
        token_consume_focal: int = 0
    ) -> str:
        """Process a code component to generate a docstring."""
        # Set visualization state
        self.visualizer.set_current_component(focal_component, file_path)
        
        # Cache file-level context first
        self._current_file = file_path
        self._cache_file_context(file_path, ast_tree, dependency_graph)
        
        # Determine which writer to use based on file extension
        writer = self.py_writer if not file_path.endswith(('.cpp', '.hpp', '.cc', '.h', '.cxx')) else self.cpp_writer
        
        # Determine component type and cache accordingly
        is_method = '.' in (focal_node_dependency_path or '')
        if is_method:
            # Handle class method
            class_name = focal_node_dependency_path.split('.')[-2]
            method_name = focal_node_dependency_path.split('.')[-1]
            
            # Cache class context if not already cached
            self._cache_class_context(class_name, ast_node, ast_tree, dependency_graph)
            self._current_class = class_name
            
            # Cache method context
            if method_name not in self._method_context_cache:
                self._method_context_cache[method_name] = {}  # Initialize method cache
            
            # Combine contexts: file -> class -> method
            combined_context = self._get_combined_context(file_path, class_name, method_name)
            if combined_context:
                self.context = combined_context
        else:
            # Handle standalone function
            self._current_class = None
            if focal_node_dependency_path:  # If it's a function
                self._cache_function_context(focal_node_dependency_path, ast_node, ast_tree, dependency_graph)
                # Combine contexts: file -> function
                if focal_node_dependency_path in self._function_context_cache:
                    self.context = self._merge_contexts(
                        self._file_context_cache.get(file_path, {}),
                        self._function_context_cache[focal_node_dependency_path]
                    )
        
        # Initialize context and counters
        self.context = ""
        reader_search_attempts = 0
        verifier_rejection_count = 0
        
        while True:
            # Step 1: Reader determines if more context is needed
            self.visualizer.update('reader', "Analyzing code component...")
            reader_response = self.reader.process(
                focal_component,
                self.context
            )
            # add reader_response to reader's memory (assistant)
            self.reader.add_to_memory("assistant", reader_response)
            
            # Step 2: Check if more information is needed
            match = re.search(r'<INFO_NEED>(.*?)</INFO_NEED>', reader_response, re.DOTALL)
            needs_info = match and match.group(1).strip().lower() == 'true'
            
            if needs_info and reader_search_attempts < self.max_reader_search_attempts:
                reader_search_attempts += 1
                self.visualizer.update('reader', f"Need more information (attempt {reader_search_attempts}/{self.max_reader_search_attempts}), ask Searcher to search additional context...")
                if self.test_mode != "context_print":
                    time.sleep(self.status_sleep_time)
                # Use Searcher to gather more information
                self.visualizer.update('searcher', "Searching for additional context...")
                if self.test_mode != "context_print":
                    time.sleep(self.status_sleep_time)
                search_results = self.searcher.process(reader_response, ast_node, ast_tree, dependency_graph, focal_node_dependency_path)
                self._update_context(search_results, token_consume_focal)
                # Refresh reader's memory with new context
                self.reader.refresh_memory([
                    {"role": "system", "content": self.reader.system_prompt},
                    {"role": "user", "content": f"Current context:\n{self.context}"}
                ])
                self.visualizer.update('reader', "Search complete, Context updated, restarting analysis...")
                if self.test_mode != "context_print":
                    time.sleep(self.status_sleep_time)
                continue
            elif needs_info:
                self.visualizer.update('reader', f"Max search attempts ({self.max_reader_search_attempts}) reached, proceeding with current context...")
                if self.test_mode != "context_print":
                    time.sleep(self.status_sleep_time)

            self.visualizer.update('reader', "No additional context needed, starting docstring generation...")
            if self.test_mode != "context_print":
                time.sleep(self.status_sleep_time)
            
            # If in reader_searcher test mode, return after context gathering
            if self.test_mode == "reader_searcher":
                return reader_response
            
            while True:  # Inner loop for writer-verifier cycle
                # Step 3: When enough context is gathered, use Writer to generate docstring
                self.visualizer.update('writer', "Generating docstring...")
                if self.test_mode != "context_print":
                    time.sleep(self.status_sleep_time)
                
                # Print context if in context_print test mode
                if self.test_mode == "context_print":
                    print("\n=== CONTEXT BEFORE WRITER CALL ===")
                    print(self.context)
                    print("=== END OF CONTEXT ===\n")
                
                docstring = writer.process(
                    focal_component,
                    self.context
                )
                
                # assert docstring is not empty
                # add writer_response to writer's memory (assistant)
                writer.add_to_memory("assistant", docstring)

                # Step 4: Use Verifier to check the quality
                self.visualizer.update('verifier', "Verifying docstring quality...")
                verification_response = self.verifier.process(
                    focal_component,
                    docstring,
                    self.context
                )
                
                # Step 5: Parse and process verification results
                verification_result = self._parse_verifier_response(verification_response)
                
                if not verification_result['needs_revision'] or verifier_rejection_count >= self.max_verifier_rejections:
                    if verifier_rejection_count >= self.max_verifier_rejections:
                        self.visualizer.update('verifier', f"Max rejection attempts ({self.max_verifier_rejections}) reached, accepting current docstring.")
                    else:
                        self.visualizer.update('verifier', "Docstring generated successfully! No need for revision.")
                    if self.test_mode != "context_print":
                        time.sleep(self.status_sleep_time)
                    return docstring
                # if needs_revision is true, then needs_context is true
                else:
                    verifier_rejection_count += 1
                    # clean verifier's memory
                    self.verifier.clear_memory()
                    if verification_result['needs_context'] and reader_search_attempts < self.max_reader_search_attempts:
                        self.visualizer.update('verifier', f"Need more context (rejection {verifier_rejection_count}/{self.max_verifier_rejections}), hands back to reader...")
                        if self.test_mode != "context_print":
                            time.sleep(self.status_sleep_time)
                        # Add context suggestion to reader's memory and break inner loop to get more context
                        self.reader.add_to_memory(
                            "user",
                            f"Additional context needed: {verification_result['context_suggestion']}"
                        )

                        # clean writer's and verifier's memory
                        self.writer.clear_memory()
                        
                        break  # Break inner loop to return to reader-searcher cycle
                    else:
                        self.visualizer.update('verifier', f"Content is not good enough (rejection {verifier_rejection_count}/{self.max_verifier_rejections}), hands back to writer...")
                        if self.test_mode != "context_print":
                            time.sleep(self.status_sleep_time)
                        # Add improvement suggestion to writer's memory and continue inner loop
                        writer.add_to_memory(
                            "user",
                            f"Please improve the docstring based on this suggestion: {verification_result['suggestion']}"
                        )
                        # Clean writer's memory but keep latest improvement suggestion
                        latest_memory = [writer.memory[-2], writer.memory[-1]] if len(writer.memory) >= 2 else writer.memory
                        writer.clear_memory()
                        writer.memory.extend(latest_memory)
                        continue  # Continue inner loop to generate new docstring

    def _update_context(self, search_results: Dict[str, Any], token_consume_focal: int) -> None:
        """Update the context with new search results by merging content within existing XML tags.
        
        Args:
            search_results: Dictionary containing new context information structured as:
                {
                    'internal': {
                        'calls': {
                            'class': {'class1': 'content1', ...},
                            'function': {'func1': 'content1', ...},
                            'method': {'method1': 'content1', ...},
                        },
                        'called_by': ['code snippet1', ...]
                    },
                    'external': {
                        'query1': 'result1',
                        'query2': 'result2'
                    }
                }
        """
        if not self.context:
            # Initialize empty context structure if none exists
            self.context = """<CONTEXT>
<INTERNAL_INFO>
<CLASS>
</CLASS>
<FUNCTION>
</FUNCTION>
<METHOD>
</METHOD>
<CALL_BY>
</CALL_BY>
</INTERNAL_INFO>
<EXTERNAL_RETRIEVAL_INFO>
</EXTERNAL_RETRIEVAL_INFO>
</CONTEXT>"""

        if 'internal' in search_results:
            internal_info = search_results['internal']
            
            # Handle calls (class, function, method)
            if 'calls' in internal_info:
                calls = internal_info['calls']
                
                # Helper function to safely update XML content
                def update_xml_section(tag: str, content_list: list) -> None:
                    if not content_list:
                        return
                    pattern = f'<{tag}>(.*?)</{tag}>'
                    match = re.search(pattern, self.context, re.DOTALL)
                    if not match:
                        # If pattern doesn't exist, something is wrong with context structure
                        return
                    existing_text = match.group(1).strip()
                    new_content = existing_text + "\n" + "\n".join(content_list) if existing_text else "\n".join(content_list)
                    # Escape backslashes in new_content to prevent regex interpretation issues
                    new_content = new_content.replace('\\', '\\\\')
                    self.context = re.sub(pattern, f'<{tag}>\n{new_content}\n</{tag}>', self.context, flags=re.DOTALL)
                
                # Update class calls
                if 'class' in calls:
                    class_content = [f"<{class_name}>{content}</{class_name}>" for class_name, content in calls['class'].items()]
                    update_xml_section('CLASS', class_content)

                # Update function calls
                if 'function' in calls:
                    func_content = [f"<{func_name}>{content}</{func_name}>" for func_name, content in calls['function'].items()]
                    update_xml_section('FUNCTION', func_content)

                # Update method calls
                if 'method' in calls:
                    method_content = [f"<{method_name}>{content}</{method_name}>" for method_name, content in calls['method'].items()]
                    update_xml_section('METHOD', method_content)

            # Update called_by
            if 'called_by' in internal_info:
                called_by_content = internal_info['called_by']
                update_xml_section('CALL_BY', called_by_content)

        # Update external info
        if 'external' in search_results:
            external_content = []
            for query, result in search_results['external'].items():
                external_content.append(f"<QUERY>{query}</QUERY>")
                external_content.append(f"<r>{result}</r>")
            update_xml_section('EXTERNAL_RETRIEVAL_INFO', external_content) 
        
        # Apply context length constraint for all models
        if hasattr(self, 'config') and 'max_input_tokens' in self.config:
            max_input_tokens = self.config.get('max_input_tokens', 10000)
        else:
            max_input_tokens = 10000  # Default fallback
            
        self._constrain_context_length(max_input_tokens=max_input_tokens, token_consume_focal=token_consume_focal)
    
    def _constrain_context_length(self, max_input_tokens: int = 10000, token_consume_focal: int = 0) -> None:
        """Constrain context length for models by truncating the longest component.
        
        Args:
            max_input_tokens: Maximum number of tokens allowed in the input context
            token_consume_focal: Number of tokens consumed by the focal component itself
        """
        try:
            # Use tiktoken to count tokens
            encoding = tiktoken.get_encoding("cl100k_base")  # Using a common encoding
            current_tokens = len(encoding.encode(self.context))
            
            # Check if we need to truncate considering both context and focal component tokens
            if current_tokens + token_consume_focal <= max_input_tokens:
                return  # No need to truncate
            
            # Find the XML section with the most tokens to truncate
            component_tokens = {}
            components = [
                ('CODE_CONTEXT', r'<CODE_CONTEXT>(.*?)</CODE_CONTEXT>'),
                ('FOCAL_COMPONENT', r'<FOCAL_COMPONENT>(.*?)</FOCAL_COMPONENT>'),
                ('RELATED_COMPONENTS', r'<RELATED_COMPONENTS>(.*?)</RELATED_COMPONENTS>'),
                ('FOCAL_DEPENDENCIES', r'<FOCAL_DEPENDENCIES>(.*?)</FOCAL_DEPENDENCIES>'),
                ('EXTERNAL_RETRIEVAL_INFO', r'<EXTERNAL_RETRIEVAL_INFO>(.*?)</EXTERNAL_RETRIEVAL_INFO>')
            ]
            
            for name, pattern in components:
                match = re.search(pattern, self.context, re.DOTALL)
                if match:
                    content = match.group(1)
                    tokens = len(encoding.encode(content))
                    component_tokens[name] = (content, tokens)
            
            # Find the component with the most tokens
            if not component_tokens:
                return  # No components found
                
            longest_component = max(component_tokens.items(), key=lambda x: x[1][1])
            component_name = longest_component[0]
            content = longest_component[1][0]
            component_token_count = longest_component[1][1]
            
            # Calculate tokens to remove, considering focal component
            tokens_to_remove = current_tokens + token_consume_focal - max_input_tokens
            
            if tokens_to_remove <= 0:
                return  # No need to truncate
                
            # Print information about truncation
            print(f"Truncating {component_name}: removing {tokens_to_remove} tokens from {component_token_count} tokens. Current total: {current_tokens} tokens")
                
            if tokens_to_remove >= component_token_count:
                # If removing the entire component isn't enough, we'll just remove it and deal with the rest later
                new_content = ""
            else:
                # Truncate the content by removing tokens from the end
                encoded_content = encoding.encode(content)
                truncated_encoded = encoded_content[:-tokens_to_remove]
                new_content = encoding.decode(truncated_encoded)
            
            # Update the context with truncated content
            pattern = f'<{component_name}>(.*?)</{component_name}>'
            self.context = re.sub(pattern, f'<{component_name}>\n{new_content}\n</{component_name}>', self.context, flags=re.DOTALL)
            
        except Exception as e:
            print(f"Error constraining context length: {e}")

    def _cache_file_context(self, file_path: str, ast_tree: ast.AST, dependency_graph: Dict[str, List[str]]) -> None:
        """Cache context at the file level to be reused by components in the same file.
        
        Args:
            file_path: Path to the file
            ast_tree: The complete AST tree for the file
            dependency_graph: The dependency graph
        """
        if file_path not in self._file_context_cache:
            # Get file-level context using searcher with a special request
            file_context = self.searcher.process(
                f"<REQUEST><INTERNAL><CALLS><FILE>{file_path}</FILE></CALLS><CALL_BY>true</CALL_BY></INTERNAL></REQUEST>",
                None,  # No specific node for file-level context
                ast_tree,
                dependency_graph,
                file_path
            )
            self._file_context_cache[file_path] = file_context

    def _cache_function_context(self, function_name: str, ast_node: ast.AST, ast_tree: ast.AST, dependency_graph: Dict[str, List[str]]) -> None:
        """Cache context for a function to be reused.
        
        Args:
            function_name: The name of the function
            ast_node: The AST node for the function 
            ast_tree: The complete AST tree
            dependency_graph: The dependency graph
        """
        if function_name not in self._function_context_cache:
            # Get function-level context using searcher
            function_context = self.searcher.process(
                f"<REQUEST><INTERNAL><CALLS><FUNCTION>{function_name}</FUNCTION></CALLS><CALL_BY>true</CALL_BY></INTERNAL></REQUEST>",
                ast_node,
                ast_tree,
                dependency_graph,
                function_name
            )
            self._function_context_cache[function_name] = function_context

    def _merge_contexts(self, *contexts: Dict[str, Any]) -> str:
        """Merge multiple context dictionaries preserving XML structure.
        
        Args:
            *contexts: Variable number of context dictionaries to merge
            
        Returns:
            Merged context string in XML format
        """
        merged = {
            'internal': {
                'calls': {
                    'class': {},
                    'function': {},
                    'method': {},
                },
                'called_by': []
            },
            'external': {}
        }
        
        # Merge all contexts
        for context in contexts:
            if not context:
                continue
                
            if 'internal' in context:
                internal = context['internal']
                if 'calls' in internal:
                    calls = internal['calls']
                    for call_type in ['class', 'function', 'method']:
                        if call_type in calls:
                            merged['internal']['calls'][call_type].update(calls[call_type])
                
                if 'called_by' in internal:
                    merged['internal']['called_by'].extend(internal['called_by'])
            
            if 'external' in context:
                merged['external'].update(context['external'])
        
        # Convert to XML format
        xml = "<CONTEXT>\n<INTERNAL_INFO>\n"
        
        # Add calls
        for call_type in ['CLASS', 'FUNCTION', 'METHOD']:
            xml += f"<{call_type}>\n"
            calls = merged['internal']['calls'][call_type.lower()]
            for name, content in calls.items():
                xml += f"<{name}>{content}</{name}>\n"
            xml += f"</{call_type}>\n"
        
        # Add called_by
        xml += "<CALL_BY>\n"
        for called_by in merged['internal']['called_by']:
            xml += f"{called_by}\n"
        xml += "</CALL_BY>\n"
        xml += "</INTERNAL_INFO>\n"
        
        # Add external info
        xml += "<EXTERNAL_RETRIEVAL_INFO>\n"
        for query, result in merged['external'].items():
            xml += f"<QUERY>{query}</QUERY>\n<r>{result}</r>\n"
        xml += "</EXTERNAL_RETRIEVAL_INFO>\n"
        xml += "</CONTEXT>"
        
        return xml

    def _get_combined_context(self, file_path: str, class_name: str = None, method_name: str = None) -> Optional[str]:
        """Get combined context from all caching levels.
        
        Args:
            file_path: The file path
            class_name: Optional class name
            method_name: Optional method name
            
        Returns:
            Combined context string or None if no context available
        """
        contexts = []
        
        # Add file context
        if file_path in self._file_context_cache:
            contexts.append(self._file_context_cache[file_path])
        
        # Add class context
        if class_name and class_name in self._class_context_cache:
            contexts.append(self._class_context_cache[class_name])
        
        # Add method context
        if method_name and method_name in self._method_context_cache:
            contexts.append(self._method_context_cache[method_name])
        
        if contexts:
            return self._merge_contexts(*contexts)
        return None
