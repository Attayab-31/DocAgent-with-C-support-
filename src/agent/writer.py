# Copyright (c) Meta Platforms, Inc. and affiliates
from typing import Dict, Any, Optional
from abc import abstractmethod
from .base import BaseAgent
from .reader import CodeComponentType

class Writer(BaseAgent):
    """Agent responsible for generating high-quality docstrings based on the code and context."""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize the Writer agent.
        
        Args:
            config_path: Optional path to the configuration file
        """
        super().__init__("Writer", config_path=config_path)
        
        # Base prompt that applies to all documentation
        self.base_prompt = """You are a Writer agent responsible for generating high-quality 
        docstrings that are both complete and helpful. Accessible context is provided to you for 
        generating the docstring.
        
        General Guidelines:
        1. Make docstrings actionable and specific:
           - Focus on practical usage
           - Highlight important considerations
           - Include warnings or gotchas
        
        2. Use clear, concise language:
           - Avoid jargon unless necessary
           - Use active voice
           - Be direct and specific
        
        3. Type Information:
           - Include precise type hints
           - Note any type constraints
           - Document generic type parameters
        
        4. Context and Integration:
           - Explain component relationships
           - Note any dependencies
           - Describe side effects
        
        5. Follow Google docstring format:
           - Use consistent indentation
           - Maintain clear section separation
           - Keep related information grouped"""

        self.add_to_memory("system", self.base_prompt)

        # Class-specific prompt
        self.class_prompt = """You are documenting a CLASS. Focus on describing the object it represents 
        and its role in the system.

        Required sections:
        1. Summary: 
           - One-line description focusing on WHAT the class represents
           - Avoid repeating the class name or obvious terms
           - Focus on the core purpose or responsibility
        
        2. Description: 
           - WHY: Explain the motivation and purpose behind this class
           - WHEN: Describe scenarios or conditions where this class should be used
           - WHERE: Explain how it fits into the larger system architecture
           - HOW: Provide a high-level overview of how it achieves its purpose
        
        3. Example: 
           - Show a practical, real-world usage scenario
           - Include initialization and common method calls
           - Demonstrate typical workflow

        Conditional sections:
        1. Parameters (if class's __init__ has parameters):
           - Focus on explaining the significance of each parameter
           - Include valid value ranges or constraints
           - Explain parameter relationships if they exist
        
        2. Attributes:
           - Explain the purpose and significance of each attribute
           - Include type information and valid values
           - Note any dependencies between attributes"""

        # Function/Method-specific prompt
        self.function_prompt = """You are documenting a FUNCTION or METHOD. Focus on describing 
        the action it performs and its effects.

        Required sections:
        1. Summary:
           - One-line description focusing on WHAT the function does
           - Avoid repeating the function name
           - Emphasize the outcome or effect
        
        2. Description:
           - WHY: Explain the purpose and use cases
           - WHEN: Describe when to use this function
           - WHERE: Explain how it fits into the workflow
           - HOW: Provide high-level implementation approach

        Conditional sections:
        1. Args (if present):
           - Explain the significance of each parameter
           - Include valid value ranges or constraints
           - Note any parameter interdependencies
        
        2. Returns:
           - Explain what the return value represents
           - Include possible return values or ranges
           - Note any conditions affecting the return value
        
        3. Raises:
           - List specific conditions triggering each exception
           - Explain how to prevent or handle exceptions
        
        4. Examples (if public and not abstract):
           - Show practical usage scenarios
           - Include common parameter combinations
           - Demonstrate error handling if relevant"""

    def is_class_component(code: str) -> bool:
        """Determine if the given code component is a class definition.
        
        Args:
            code: The code component to analyze
            
        Returns:
            bool: True if the component is a class definition, False otherwise
        """
        return "class " in code.split('\n')[0]

    def get_custom_prompt(self, code: str) -> str:
        """Get the appropriate system prompt based on the component type.
        
        Args:
            code: The code component to analyze
            
        Returns:
            str: The appropriate system prompt for the component type
        """
        is_class = Writer.is_class_component(code)
        specific_prompt = self.class_prompt if is_class else self.function_prompt
        return specific_prompt

    def _ensure_docstring_format(self, response: str) -> str:
        """Ensure the response has proper DOCSTRING XML tags.
        If tags are missing but content looks like a docstring, wrap it.
        If content is invalid, return empty string.
        
        Args:
            response: Raw response from LLM
            
        Returns:
            Properly formatted docstring with XML tags
        """
        # If already has proper tags, return as is
        if "<DOCSTRING>" in response and "</DOCSTRING>" in response:
            return response

        # If no tags but looks like a docstring (has indentation, starts with description)
        lines = response.strip().splitlines()
        if lines and not any(line.strip().startswith(('<', '/', '<?xml')) for line in lines):
            # Looks like valid docstring content, wrap it
            return f"<DOCSTRING>\n{response.strip()}\n</DOCSTRING>"

        # Not valid docstring content
        return "<DOCSTRING></DOCSTRING>"

    def extract_docstring(self, response: str) -> str:
        """Extract the docstring from the LLM response.
        
        Args:
            response: The full response from the LLM containing the docstring between XML tags
            
        Returns:
            str: The extracted docstring, or empty string if no docstring found
        """
        if not response:
            return ""

        response = self._ensure_docstring_format(response)
        
        try:
            # Find content between DOCSTRING tags
            start_tag = "<DOCSTRING>"
            end_tag = "</DOCSTRING>"
            start_idx = response.index(start_tag) + len(start_tag)
            end_idx = response.index(end_tag)
            
            content = response[start_idx:end_idx].strip()
            if not content:
                return ""

            # Validate basic docstring structure
            lines = content.splitlines()
            if not lines:
                return ""

            # Remove triple quotes if they exist
            content = content.strip('"""').strip("'''").strip()
            return content

        except (ValueError, AttributeError) as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error extracting docstring from response: {str(e)}")
            return ""

    def process(
        self,
        focal_component: str,
        context: Dict[str, Any],
    ) -> str:
        """Generate a docstring for the given code component.
        
        Args:
            focal_component: The code component needing a docstring
            context: Dictionary containing gathered context information
            
        Returns:
            str: The generated docstring following the specified format
        """
        
        task_description = f"""
        Available context:
        {context}

        {self.get_custom_prompt(focal_component)}

        Now, generate a high-quality docstring for the following Code Component based on the Available context:
        
        <FOCAL_CODE_COMPONENT>
        {focal_component}
        </FOCAL_CODE_COMPONENT>

        Keep in mind:
        1. Generate docstring between XML tag: <DOCSTRING> and </DOCSTRING>
        2. First analysis the code component and then generate the docstring at the end based on the context.
        3. Do not add triple quotes (\"\"\") to your generated docstring.
        4. Always double check if the generated docstring is within the XML tags: <DOCSTRING> and </DOCSTRING>. This is critical for parsing the docstring.
        """
        self.add_to_memory("user", task_description)
        
        # Generate response using LLM
        full_response = self.generate_response()
        
        # Extract and return just the docstring part
        return self.extract_docstring(full_response)

