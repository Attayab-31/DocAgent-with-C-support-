# Copyright (c) Meta Platforms, Inc. and affiliates

from typing import Optional, List
from .base import BaseAgent
import re


class Verifier(BaseAgent):
    """Agent responsible for verifying the quality of generated docstrings."""
    
    def __init__(self, config_path: Optional[str] = None):
        super().__init__(config_path)
        
        # Set the system prompt for docstring verification
        self.system_prompt = """You are a docstring quality verifier.
        
        Review the following aspects of docstring quality:
        
        1. Clarity and Relevance:
           - Check for clear, concise explanations
           - Verify technical accuracy matches code behavior
           - Flag any unclear, ambiguous, or contradictory descriptions
        
        2. Appropriate Detail Level:
           - Flag overly detailed technical explanations of implementation
           - Ensure focus is on usage and purpose, not line-by-line explanation
           - Check if internal implementation details are unnecessarily exposed
        
        3. Completeness Check:
           - Verify all required sections are present (summary, args, returns, etc.)
           - Check if each section provides meaningful information
           - Ensure critical usage information is not missing
        
        Output Format:
        Your analysis must include these XML tags in order:
        1. <NEED_REVISION>true|false</NEED_REVISION>
           - Must be included and contain either 'true' or 'false'
           - Indicates if docstring needs improvement
        
        2. If NEED_REVISION is true:
           <MORE_CONTEXT>true|false</MORE_CONTEXT>
           - Must be included and contain either 'true' or 'false'
           - Indicates if additional context is required for improvement
           - Only request more context if absolutely necessary, as context gathering is expensive and may fail
           
           3. Based on MORE_CONTEXT value:
              - If true: <SUGGESTION_CONTEXT>explanation of what specific context is needed</SUGGESTION_CONTEXT>
              - If false: <SUGGESTION>specific improvement suggestions</SUGGESTION>
           
           4. Explain your analysis after the tags: what works well, what needs improvement, etc.
        """
        self.add_to_memory("system", self.system_prompt)

    def _ensure_valid_xml(self, response: str) -> str:
        """Ensure response has all required XML tags with valid values."""
        if not response:
            return self._create_error_response("Empty response")
            
        # Add NEED_REVISION if missing
        need_revision_match = re.search(r'<NEED_REVISION>(.*?)</NEED_REVISION>', response, re.DOTALL)
        if not need_revision_match:
            response = "<NEED_REVISION>false</NEED_REVISION>\n" + response
        elif need_revision_match.group(1).strip().lower() not in ['true', 'false']:
            response = response.replace(
                need_revision_match.group(1),
                'false'
            )

        need_revision_value = need_revision_match and need_revision_match.group(1).strip().lower() == 'true' if need_revision_match else False

        if need_revision_value:
            # Check and add MORE_CONTEXT if missing
            more_context_match = re.search(r'<MORE_CONTEXT>(.*?)</MORE_CONTEXT>', response, re.DOTALL)
            if not more_context_match:
                response = response.replace('</NEED_REVISION>', '</NEED_REVISION>\n<MORE_CONTEXT>false</MORE_CONTEXT>')
            elif more_context_match.group(1).strip().lower() not in ['true', 'false']:
                response = response.replace(
                    more_context_match.group(1),
                    'false'
                )

            more_context_value = more_context_match and more_context_match.group(1).strip().lower() == 'true' if more_context_match else False

            # Add appropriate suggestion tag based on MORE_CONTEXT value
            if more_context_value:
                suggestion_context_match = re.search(r'<SUGGESTION_CONTEXT>(.*?)</SUGGESTION_CONTEXT>', response, re.DOTALL)
                if not suggestion_context_match:
                    response = response.replace('</MORE_CONTEXT>', '</MORE_CONTEXT>\n<SUGGESTION_CONTEXT>Additional context needed to improve docstring quality.</SUGGESTION_CONTEXT>')
            else:
                suggestion_match = re.search(r'<SUGGESTION>(.*?)</SUGGESTION>', response, re.DOTALL)
                if not suggestion_match:
                    response = response.replace('</MORE_CONTEXT>', '</MORE_CONTEXT>\n<SUGGESTION>Please improve the docstring following standard documentation guidelines.</SUGGESTION>')

        return response

    def _create_error_response(self, error_msg: str) -> str:
        """Create a valid XML response for error cases."""
        return f"""<NEED_REVISION>true</NEED_REVISION>
<MORE_CONTEXT>false</MORE_CONTEXT>
<SUGGESTION>Error occurred during verification: {error_msg}. Please review and improve the docstring.</SUGGESTION>

An error occurred during verification. Please review the docstring manually."""

    def process(
        self,
        focal_component: str,
        docstring: str,
        context: str = ""
    ) -> str:
        """Verify the quality of a generated docstring.
        
        Args:
            focal_component: The code component needing a docstring
            docstring: The generated docstring to verify
            context: The context used to generate the docstring
            
        Returns:
            A string containing the verification results with proper XML tags
        """
        task_description = f"""
        Current Context:
        {context if context else 'No context was used.'}

        Verify the quality of the following docstring for this Code Component:
        
        Code Component:
        {focal_component}
        
        Generated Docstring:
        {docstring}

        Required: Your response MUST include XML tags in the following format:
        1. <NEED_REVISION>true|false</NEED_REVISION>
        2. If needs revision: <MORE_CONTEXT>true|false</MORE_CONTEXT>
        3. If more context needed: <SUGGESTION_CONTEXT>what context is needed</SUGGESTION_CONTEXT>
           If no more context needed: <SUGGESTION>improvement suggestions</SUGGESTION>
        
        Follow this with a detailed explanation of your analysis.
        """
        self.add_to_memory("user", task_description)
        
        try:
            response = self.generate_response()
            return self._ensure_valid_xml(response)            
        except Exception as e:
            return self._create_error_response(str(e))
