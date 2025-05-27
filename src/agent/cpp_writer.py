"""C++ Docstring Writer using Doxygen format"""

from typing import Dict, Any
from .writer import Writer
import re

class CppWriter(Writer):
    """Writer agent specialized for C++ docstrings using Doxygen format."""
    
    def __init__(self, config_path=None):
        super().__init__(config_path)
        self.base_prompt = """You are a Writer agent responsible for generating high-quality 
        C++ docstrings using Doxygen format. Focus on clear, practical documentation that helps
        developers understand and use the code effectively.

        Guidelines for Doxygen Documentation:
        1. Use Doxygen commands:
           - @brief for short descriptions
           - @details for detailed descriptions
           - @param[in]/[out]/[in,out] for parameters with direction
           - @tparam for template parameters
           - @return for return values
           - @throws for exceptions
           - @code/@endcode for examples
           - @note for important notes
           - @overload for overloaded methods

        2. For Classes:
           - Use @class for class descriptions
           - Document public interface thoroughly
           - Explain class purpose and relationships
           - Document member variables with @var
           - Use @see for related classes

        3. For Functions/Methods:
           - Start with @brief description
           - Use @param with direction indicators
           - Include type information in parameter docs
           - Document return values with @return
           - Document exceptions with @throws
           - Reference overloads when present
           - Provide examples with @code

        4. Style and Format:
           - Write clear, concise descriptions
           - Use proper Doxygen command syntax
           - Maintain consistent formatting
           - Group related information logically
           - Document parameter constraints"""

        # Initialize specific prompts
        self._init_prompts()

    def _init_prompts(self):
        """Initialize C++-specific prompts."""
        self.class_prompt = """You are documenting a C++ CLASS using Doxygen format.

        Required sections:
        1. @class [name] - Name and brief description
        2. @brief - Short summary of class purpose
        3. @details - Detailed description including:
           - Purpose and responsibility
           - Usage scenarios
           - Important considerations
           - Template parameters if any
        4. Member Documentation:
           - @var for member variables
           - @see for related classes/functions
        5. Example usage in @code blocks

        Example format:
        /**
         * @class MyClass
         * @brief Brief description
         *
         * @details
         * Detailed description...
         *
         * @var member_var
         * Description of member variable
         *
         * Example:
         * @code{.cpp}
         * MyClass obj;
         * obj.doSomething();
         * @endcode
         */
        """

        self.function_prompt = """You are documenting a C++ FUNCTION/METHOD using Doxygen format.

        Required sections:
        1. @brief - Short description of functionality
        2. @details - Detailed description including:
           - Purpose and usage
           - Implementation approach
           - Important notes
           - Performance considerations
        3. Parameters:
           - @param[in] for input parameters
           - @param[out] for output parameters
           - @param[in,out] for parameters modified by the function
           - Include type information and constraints
        4. @tparam - For template parameters
        5. @return - Description of return value
        6. @throws - List of exceptions
        7. @see - References to related functions/overloads
        8. @code - Usage examples

        Example format:
        /**
         * @brief Brief description
         *
         * @details
         * Detailed description...
         *
         * @param[in] param1 Description of first parameter
         * @param[out] param2 Description of second parameter
         * @return Description of return value
         * @throws std::exception Description of when/why
         *
         * @code{.cpp}
         * // Usage example
         * auto result = myFunction(param1, param2);
         * @endcode
         */
        """

    def extract_param_info(self, param_str: str) -> Dict[str, str]:
        """
        Extract parameter information from a C++ parameter declaration.
        
        Args:
            param_str: The parameter string to parse
            
        Returns:
            Dictionary with parameter info (direction, type, name)
        """
        # Remove default values for parsing
        param_str = re.sub(r'=.*?(?=,|$)', '', param_str).strip()
        
        # Determine parameter direction
        direction = "in"  # Default direction
        if "const" not in param_str and ("&" in param_str or "*" in param_str):
            direction = "in,out"
        
        # Extract type and name
        parts = param_str.split()
        if not parts:
            return {"direction": direction, "type": "", "name": param_str}
        
        name = parts[-1].replace("*", "").replace("&", "").strip()
        type_info = " ".join(parts[:-1])
        
        return {
            "direction": direction,
            "type": type_info,
            "name": name
        }

    def get_custom_prompt(self, focal_component: str) -> str:
        """Get the appropriate prompt template based on component type."""
        component_lower = focal_component.lower()
        
        if "class" in component_lower and "{" in focal_component:
            return self.class_prompt
        elif "void" in component_lower or "return" in component_lower or "(" in focal_component:
            # Extract parameter info for better context
            params_start = focal_component.find("(")
            params_end = focal_component.find(")")
            if params_start > 0 and params_end > params_start:
                params_str = focal_component[params_start+1:params_end]
                params = []
                if params_str.strip():
                    for param in params_str.split(","):
                        param_info = self.extract_param_info(param)
                        params.append(param_info)
                
                # Customize prompt with parameter info
                prompt = self.function_prompt + "\nParameter Information:\n"
                for param in params:
                    prompt += f"- {param['name']} ({param['type']}): {param['direction']} parameter\n"
                return prompt
            return self.function_prompt
        else:
            return self.base_prompt  # Default to base prompt

    def process(self, focal_component: str, context: Dict[str, Any]) -> str:
        """Generate a Doxygen-formatted C++ docstring."""
        # Add hints about overloads if detected in context
        if "related_components" in context:
            related = context["related_components"]
            if isinstance(related, list) and len(related) > 0:
                context["overload_hints"] = "This appears to be one of several overloaded methods. Remember to use @overload and cross-reference related versions."

        prompt = f"""
        Available Context:
        {context}

        {self.get_custom_prompt(focal_component)}

        Generate a Doxygen-style C++ docstring for:

        <FOCAL_CODE_COMPONENT>
        {focal_component}
        </FOCAL_CODE_COMPONENT>

        Requirements:
        1. Use proper Doxygen commands (@brief, @param, etc.)
        2. Indicate parameter direction with [in]/[out]/[in,out]
        3. Include complete type information
        4. Document any constraints or requirements
        5. Add @overload if this is an overloaded method
        6. Put docstring between <DOCSTRING> tags
        7. Do not include /** and */ markers (they will be added later)
        """

        self.add_to_memory("user", prompt)
        full_response = self.generate_response()
        return self.extract_docstring(full_response)
