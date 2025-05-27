# Copyright (c) Meta Platforms, Inc. and affiliates
from typing import Dict, List, Any, Optional
from .base import BaseAgent
from .reader import InformationRequest
from .tool.internal_traverse import ASTNodeAnalyzer  # Updated import to use only ASTNodeAnalyzer
from .tool.perplexity_api import PerplexityAPI, PerplexityResponse
import re
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET
from io import StringIO
import ast  # Keep for type annotations

@dataclass
class ParsedInfoRequest:
    """Structured format for parsed information requests.
    
    Attributes:
        internal_requests: Dictionary containing:
            - call: Dictionary with keys 'class', 'function', 'method', each containing
                   a list of code component names that are called
            - call_by: Boolean indicating if caller information is needed
        external_requests: List of query strings for external information search
    """
    internal_requests: Dict[str, Any] = field(default_factory=lambda: {
        'call': {
            'class': [],
            'function': [], 
            'method': []
        },
        'call_by': False
    })
    external_requests: List[str] = field(default_factory=list)

class Searcher(BaseAgent):
    """Agent responsible for gathering requested information from internal and external sources."""
    
    def __init__(self, repo_path: str, config_path: Optional[str] = None):
        """Initialize the Searcher agent.
        
        Args:
            repo_path: Path to the repository being analyzed
            config_path: Optional path to the configuration file
        """
        super().__init__("Searcher", config_path=config_path)
        self.repo_path = repo_path
        self.ast_analyzer = ASTNodeAnalyzer(repo_path)

    def process(
        self, 
        reader_response: str, 
        ast_node: ast.AST,
        ast_tree: ast.AST,
        dependency_graph: Dict[str, List[str]],
        focal_node_dependency_path: str
    ) -> Dict[str, Any]:
        """Process the reader's response and gather the requested information.
        
        Args:
            reader_response: Response from the Reader agent containing
                           information requests in structured XML format
            ast_node: AST node representing the focal component
            ast_tree: AST tree for the entire file
            dependency_graph: Dictionary mapping component paths to their dependencies
            focal_node_dependency_path: Dependency path of the focal component
                        
        Returns:
            A dictionary containing the gathered information, structured as:
            {
                'internal': {
                    'calls': {
                        'class': ['class1': 'content1', 'class2': 'content2', ...],
                        'function': ['func1': 'content1', 'func2': 'content2', ...],
                        'method': ['method1': 'content1', 'method2': 'content2', ...],
                        },
                    'called_by': ['code snippet1', 'code snippet2', ...],
                },
                'external': {
                    'query1': 'result1',
                    'query2': 'result2'
                }
            }
        """
        # Parse the reader's response into structured format
        parsed_request = self._parse_reader_response(reader_response)

        # Gather internal information using dependency graph and AST analyzer
        internal_info = self._gather_internal_info(
            ast_node,
            ast_tree,
            focal_node_dependency_path,
            dependency_graph,
            parsed_request
        )

        # Gather external information using Perplexity API
        external_info = self._gather_external_info(parsed_request.external_requests)
        
        return {
            'internal': internal_info,
            'external': external_info
        }

    def _ensure_valid_xml(self, response: str) -> str:
        """Ensure XML content has all required tags with valid structure.
        
        Args:
            response: Raw response text potentially containing XML
            
        Returns:
            Valid XML string with all required tags
        """
        # If no REQUEST tag found, wrap content
        if "<REQUEST>" not in response or "</REQUEST>" not in response:
            xml_content = f"<REQUEST>\n{response}\n</REQUEST>"
        else:
            # Extract existing REQUEST content
            xml_match = re.search(r'<REQUEST>(.*?)</REQUEST>', response, re.DOTALL)
            xml_content = f'<REQUEST>{xml_match.group(1) if xml_match else ""}</REQUEST>'

        # Parse to see what's missing
        try:
            root = ET.fromstring(xml_content)
            
            # Check and add INTERNAL if missing
            internal = root.find('INTERNAL')
            if internal is None:
                internal = ET.SubElement(root, 'INTERNAL')

            # Check and add CALLS if missing
            calls = internal.find('CALLS')
            if calls is None:
                calls = ET.SubElement(internal, 'CALLS')

            # Ensure CLASS, FUNCTION, METHOD tags exist
            for tag in ['CLASS', 'FUNCTION', 'METHOD']:
                if calls.find(tag) is None:
                    ET.SubElement(calls, tag)

            # Check and add CALL_BY if missing
            call_by = internal.find('CALL_BY')
            if call_by is None:
                call_by = ET.SubElement(internal, 'CALL_BY')
                call_by.text = 'false'

            # Check and add RETRIEVAL/QUERY if missing
            retrieval = root.find('RETRIEVAL')
            if retrieval is None:
                retrieval = ET.SubElement(root, 'RETRIEVAL')
                query = ET.SubElement(retrieval, 'QUERY')
                query.text = ''

            # Convert back to string
            return ET.tostring(root, encoding='unicode', method='xml')
            
        except ET.ParseError:
            # If XML is completely invalid, return minimal valid structure
            return """
<REQUEST>
    <INTERNAL>
        <CALLS>
            <CLASS></CLASS>
            <FUNCTION></FUNCTION>
            <METHOD></METHOD>
        </CALLS>
        <CALL_BY>false</CALL_BY>
    </INTERNAL>
    <RETRIEVAL>
        <QUERY></QUERY>
    </RETRIEVAL>
</REQUEST>
"""

    def _parse_comma_list(self, text: str | None) -> List[str]:
        """Parse comma-separated text into a list, handling None values."""
        if not text:
            return []
        return [x.strip() for x in text.split(',') if x.strip()]

    def _parse_reader_response(self, reader_response: str) -> ParsedInfoRequest:
        """Parse the reader's structured XML response.
        
        Args:
            reader_response: Response from Reader agent containing XML
            
        Returns:
            ParsedInfoRequest object containing structured requests
        """
        try:
            # Get valid XML structure 
            xml_content = self._ensure_valid_xml(reader_response)
            
            # Parse XML
            root = ET.fromstring(xml_content)
            
            # Parse internal requests
            internal = root.find('INTERNAL')
            calls = internal.find('CALLS')
            internal_requests = {
                'call': {
                    'class': self._parse_comma_list(calls.find('CLASS').text),
                    'function': self._parse_comma_list(calls.find('FUNCTION').text),
                    'method': self._parse_comma_list(calls.find('METHOD').text)
                },
                'call_by': internal.find('CALL_BY').text.lower() == 'true' if internal.find('CALL_BY') is not None else False
            }
            
            # Parse external requests
            external = root.find('RETRIEVAL')
            external_requests = self._parse_comma_list(external.find('QUERY').text) if external is not None and external.find('QUERY') is not None else []
            
            return ParsedInfoRequest(
                internal_requests=internal_requests,
                external_requests=external_requests
            )
            
        except (ET.ParseError, AttributeError) as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error parsing XML in reader response: {str(e)}")
            # Return empty request if XML parsing fails
            return ParsedInfoRequest()
    
    def _gather_internal_info(
        self, 
        ast_node: ast.AST,
        ast_tree: ast.AST,
        focal_dependency_path: str,
        dependency_graph: Dict[str, List[str]],
        parsed_request: ParsedInfoRequest
    ) -> Dict[str, Any]:
        """Gather internal information using the dependency graph and AST analyzer.
        
        Args:
            ast_node: AST node representing the focal component
            ast_tree: AST tree for the entire file
            focal_dependency_path: Dependency path of the focal component
            dependency_graph: Dictionary mapping component paths to their dependencies
            parsed_request: Structured format of information requests
            
        Returns:
            Dictionary containing gathered internal information structured as:
            {
                'calls': {
                    'class': {'class_name': 'code_content', ...},
                    'function': {'function_name': 'code_content', ...},
                    'method': {'method_name': 'code_content', ...}
                },
                'called_by': ['code_snippet1', 'code_snippet2', ...]
            }
        """
        result = {
            'calls': {
                'class': {},
                'function': {},
                'method': {}
            },
            'called_by': []
        }
        
        # Get dependencies of the focal component from the dependency graph
        component_dependencies = dependency_graph.get(focal_dependency_path, [])
        
        # Process class dependencies
        if parsed_request.internal_requests['call']['class']:
            requested_classes = parsed_request.internal_requests['call']['class']
            for dependency_path in component_dependencies:
                # Check if this is a class dependency by looking at capitalization of the last part
                path_parts = dependency_path.split('.')
                if path_parts and path_parts[-1][0].isupper():
                    # This looks like a class dependency
                    class_name = path_parts[-1]
                    
                    # Check if this class is in the requested classes
                    # Use flexible matching for partial class names or with prefixes
                    for requested_class in requested_classes:
                        # Match by exact name, or as part of a path
                        if (requested_class == class_name or 
                            requested_class in dependency_path or 
                            class_name.endswith(requested_class)):
                            
                            # Get the class initialization code
                            class_code = self.ast_analyzer.get_component_by_path(
                                ast_node, 
                                ast_tree, 
                                dependency_path
                            )
                            
                            if class_code:
                                result['calls']['class'][requested_class] = class_code
                                break
        
        # Process function dependencies
        if parsed_request.internal_requests['call']['function']:
            requested_functions = parsed_request.internal_requests['call']['function']
            for dependency_path in component_dependencies:
                # Check if this is likely a function (last part starts with lowercase)
                path_parts = dependency_path.split('.')
                if path_parts and path_parts[-1][0].islower():
                    # This looks like a function or method, differentiate by checking if it's in a class
                    # If the second-to-last part starts with uppercase, it's likely a method
                    if len(path_parts) >= 2 and path_parts[-2][0].isupper():
                        # This is likely a method, skip for now
                        continue
                        
                    function_name = path_parts[-1]
                    
                    # Check if this function is in the requested functions
                    for requested_function in requested_functions:
                        # Match by exact name, or as part of a path
                        if (requested_function == function_name or 
                            requested_function in dependency_path or 
                            function_name.endswith(requested_function)):
                            
                            # Get the function code
                            function_code = self.ast_analyzer.get_component_by_path(
                                ast_node, 
                                ast_tree, 
                                dependency_path
                            )
                            
                            if function_code:
                                result['calls']['function'][requested_function] = function_code
                                break
        
        # Process method dependencies
        if parsed_request.internal_requests['call']['method']:
            requested_methods = parsed_request.internal_requests['call']['method']
            for dependency_path in component_dependencies:
                # Check if this is likely a method (part after a part that starts with uppercase)
                path_parts = dependency_path.split('.')
                if len(path_parts) >= 2 and path_parts[-1][0].islower() and path_parts[-2][0].isupper():
                    method_name = path_parts[-1]
                    class_name = path_parts[-2]
                    full_method_name = f"{class_name}.{method_name}"
                    
                    # Check if this method is in the requested methods
                    for requested_method in requested_methods:
                        # Match by exact name, class.method, or just method name
                        if (requested_method == full_method_name or 
                            requested_method == method_name or 
                            requested_method in dependency_path or
                            method_name.endswith(requested_method)):
                            
                            # Get the method code
                            method_code = self.ast_analyzer.get_component_by_path(
                                ast_node, 
                                ast_tree, 
                                dependency_path
                            )
                            
                            if method_code:
                                result['calls']['method'][requested_method] = method_code
                                break
        
        # Handle call_by (what calls this component)
        if parsed_request.internal_requests['call_by']:
            parent_components = self.ast_analyzer.get_parent_components(
                ast_node, 
                ast_tree, 
                focal_dependency_path,
                dependency_graph
            )
            
            if parent_components:
                result['called_by'].extend(parent_components)
            else:
                result['called_by'].append("This component is never called by any other component.")
        
        return result

    def _gather_external_info(self, queries: List[str]) -> Dict[str, str]:
        """Gather external information using Perplexity API.
        
        Args:
            queries: List of search queries
            
        Returns:
            Dictionary mapping queries to their responses
        """
        if not queries:
            return {}
            
        try:
            perplexity = PerplexityAPI()
            responses = perplexity.batch_query(
                questions=queries,
                system_prompt="You are a helpful assistant providing concise and accurate information about programming concepts and code. Focus on technical accuracy and clarity.",
                temperature=0.1
            )
            
            # Create mapping of queries to responses
            results = {}
            for query, response in zip(queries, responses):
                if response is not None:
                    results[query] = response.content
                else:
                    results[query] = "Error: Failed to get response from Perplexity API"
                    
            return results
            
        except Exception as e:
            print(f"Error using Perplexity API: {str(e)}")
            return {query: f"Error: {str(e)}" for query in queries}