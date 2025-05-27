"""Utilities for handling C++ docstrings."""

import os
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

def cleanup_docstring(docstring: str) -> str:
    """
    Clean up a docstring by removing duplicate commands and fixing common formatting issues.
    
    Args:
        docstring: The docstring to clean up
        
    Returns:
        The cleaned docstring
    """
    # Split into lines
    lines = docstring.split('\n')
    cleaned_lines = []
    seen_commands = set()
    in_docstring = False
    current_command = None
    
    for line in lines:
        line = line.strip()
        
        # Handle docstring boundaries
        if line.startswith('/**'):
            if not in_docstring:
                in_docstring = True
                cleaned_lines.append('/**')
            continue
        elif line.startswith('*/'):
            if in_docstring:
                in_docstring = False
                cleaned_lines.append(' */')
            continue
            
        if not in_docstring:
            continue
            
        # Clean up line
        line = line.lstrip('*').strip()
        
        # Handle commands
        if line.startswith('@'):
            parts = line.split(maxsplit=1)
            command = parts[0]
            
            # Always allow certain commands to repeat
            if command in ['@param', '@tparam', '@throws', '@see']:
                cleaned_lines.append(f' * {line}')
            
        # Handle Doxygen commands
        if line.startswith('@'):
            command = line.split()[0]
            # Skip duplicate commands unless they're allowed to repeat (like @param)
            if command not in seen_commands or command in ['@param', '@tparam', '@throws', '@see']:
                seen_commands.add(command)
                cleaned_lines.append(line)
        else:
            cleaned_lines.append(line)
            
    return '\n'.join(cleaned_lines)

def insert_cpp_docstring(file_path: str, docstring: str, start_line: int, signature: str = "", context: str = "") -> bool:
    """
    Insert or update a docstring in a C++ file.
    
    Args:
        file_path: The path to the C++ file
        docstring: The docstring to insert
        start_line: The line number where the docstring should be inserted (1-based)
        signature: Optional function/method signature for overload detection
        context: Optional surrounding code context for better overload handling
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Read the original file
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # Find the actual declaration line by looking for non-whitespace
        decl_idx = start_line - 1  # Convert to 0-based
        while decl_idx < len(lines) and not lines[decl_idx].strip():
            decl_idx += 1
            
        # Find where to insert the docstring (before any existing comments)
        insert_idx = decl_idx
        while insert_idx > 0:
            line = lines[insert_idx - 1].strip()
            if not line or line.startswith('//') or line.startswith('/*'):
                insert_idx -= 1
            else:
                break
                
        # Remove any existing docstrings
        i = insert_idx
        while i > 0:
            line = lines[i - 1].strip()
            if line.startswith('/**'):
                j = i
                while j < len(lines) and '*/' not in lines[j]:
                    j += 1
                if j < len(lines) and '*/' in lines[j]:
                    del lines[i-1:j+1]
                    insert_idx = i-1
                break
            elif line and not (line.startswith('//') or line.startswith('/*')):
                break
            i -= 1
            
        # Get proper indentation
        base_indent = ""
        if decl_idx < len(lines):
            line = lines[decl_idx]
            base_indent = line[:len(line) - len(line.lstrip())]
            
        # Format the docstring
        docstring_lines = format_cpp_docstring(docstring)
        if not docstring_lines:
            return True
            
        # Apply indentation
        indented_lines = []
        for line in docstring_lines:
            if line.strip():
                indented_lines.append(f"{base_indent}{line}\n")
            else:
                indented_lines.append("\n")
                
        # Ensure spacing
        if insert_idx > 0 and lines[insert_idx - 1].strip():
            indented_lines.insert(0, "\n")
        if lines[insert_idx].strip():
            indented_lines.append("\n")
            
        # Insert the docstring
        lines[insert_idx:insert_idx] = indented_lines
        
        # Write back to file
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        return True
        
    except Exception as e:
        logger.error(f"Error inserting C++ docstring: {e}")
        return False

def format_cpp_docstring(docstring: str) -> List[str]:
    """
    Format a docstring for C++ style documentation using Doxygen format.
    
    Args:
        docstring: The docstring to format
        
    Returns:
        A list of formatted lines ready for insertion
    """
    if not docstring or not docstring.strip():
        return []

    # Start with the opening comment
    processed_lines = ['/**']
    
    # Track docstring sections
    in_code_block = False
    current_section = None
    param_section = False
    first_line = True
    
    # Split and process each line
    for line in docstring.split('\n'):
        # Clean up the line
        cleaned = line.strip()
        if not cleaned:
            if in_code_block:
                processed_lines.append(' *')
            continue
        
        # Remove existing comment markers
        cleaned = cleaned.lstrip('*').lstrip('/').strip()
        if cleaned.startswith('@') and ' ' in cleaned:
            command, content = cleaned.split(' ', 1)
            command = command.strip()
            content = content.strip()
            
            # Handle different Doxygen commands
            if command == '@brief':
                if not first_line:  # Add spacing if not the first line
                    processed_lines.append(' *')
                processed_lines.append(f' * @brief {content}')
                current_section = 'brief'
            elif command == '@details':
                processed_lines.append(' *')
                processed_lines.append(f' * @details')
                if content:
                    processed_lines.append(f' * {content}')
                current_section = 'details'
            elif command == '@param':
                if not param_section:
                    processed_lines.append(' *')
                    param_section = True
                # Ensure proper [in] tag
                if '[' not in content:
                    content = f'[in] {content}'
                processed_lines.append(f' * @param {content}')
            elif command in ['@return', '@returns']:
                processed_lines.append(' *')
                processed_lines.append(f' * @return {content}')
            elif command == '@code':
                if not cleaned.endswith('.cpp'):
                    processed_lines.append(' * @code{.cpp}')
                else:
                    processed_lines.append(f' * {cleaned}')
                in_code_block = True
            elif command == '@endcode':
                processed_lines.append(f' * {cleaned}')
                in_code_block = False
            else:
                # Other commands
                processed_lines.append(f' * {cleaned}')
        else:
            # Regular text line
            if in_code_block:
                # Preserve indentation in code blocks
                indent = len(line) - len(line.lstrip())
                processed_lines.append(f' *     {" " * indent}{cleaned}')
            else:
                processed_lines.append(f' * {cleaned}')
        
        first_line = False
    
    # Close the comment block
    processed_lines.append(' */')
    
    return processed_lines

def get_indentation(line: str) -> str:
    """
    Get the leading whitespace from a line of code.
    
    Args:
        line: The line to analyze
        
    Returns:
        The indentation string
    """
    return line[:len(line) - len(line.lstrip())]

def apply_indentation(lines: List[str], base_indent: str) -> List[str]:
    """
    Apply proper indentation to docstring lines.
    
    Args:
        lines: List of docstring lines to indent
        base_indent: The base indentation string to use
        
    Returns:
        List of properly indented lines
    """
    # Add base indentation to each non-empty line
    indented = []
    for line in lines:
        if line.strip():
            # Ensure consistent spacing after comment markers
            if line.strip().startswith('*'):
                # Make sure there's exactly one space after the asterisk
                content = line.strip()
                if len(content) > 1 and content[1] != ' ':
                    content = content[0] + ' ' + content[1:].lstrip()
                indented.append(base_indent + content)
            else:
                indented.append(base_indent + line.strip())
        else:
            indented.append("")
    return indented
    result = []
    for line in lines:
        if line.strip() == '/**' or line.strip() == '*/':
            # Comment delimiters get base indentation
            result.append(base_indent + line.strip())
        else:
            # Other lines get base indentation plus one level
            result.append(base_indent + line)
    return result

def parse_cpp_signature(signature: str) -> tuple:
    """
    Parse a C++ function/method signature into name and parameter list.
    
    Args:
        signature: The function/method signature to parse
        
    Returns:
        Tuple of (function_name, [parameter_list])
    """
    # Extract function name
    name_end = signature.find('(')
    if name_end == -1:
        return "", []
    
    name = signature[:name_end].strip().split()[-1]  # Get last word before parentheses
    
    # Extract parameters
    params_str = signature[name_end+1:signature.rfind(')')]
    if not params_str.strip():
        return name, []
    
    # Split parameters and clean them up
    params = []
    param_list = params_str.split(',')
    for param in param_list:
        param = param.strip()
        if param:
            # Extract parameter name (last word before any = or , or end)
            parts = param.split()
            if parts:
                param_name = parts[-1].split('=')[0].strip()
                params.append((param, param_name))
    
    return name, params

def is_overloaded_method(signature: str, content: str) -> bool:
    """
    Check if a method is overloaded by looking for other declarations.
    
    Args:
        signature: The method signature to check
        content: The content to search in
        
    Returns:
        True if the method appears to be overloaded
    """
    name, _ = parse_cpp_signature(signature)
    if not name:
        return False
    
    # Look for other declarations of the same name
    lines = content.split('\n')
    count = 0
    for line in lines:
        if name in line and '(' in line and ')' in line:
            count += 1
            if count > 1:
                return True
    
    return False

def format_overloaded_docstring(docstring: str, signature: str, content: str) -> str:
    """
    Format docstring for an overloaded method, adding appropriate Doxygen commands.
    
    Args:
        docstring: The docstring to format
        signature: The method signature
        content: The surrounding code content
        
    Returns:
        Formatted docstring with overload information
    """
    name, params = parse_cpp_signature(signature)
    if not name:
        return docstring
    
    # Add overload markers
    lines = docstring.split('\n')
    new_lines = []
    brief_added = False
    
    for line in lines:
        if '@brief' in line and not brief_added:
            # Add overload note to brief
            new_lines.append(line)
            new_lines.append(' * @overload')
            brief_added = True
        else:
            new_lines.append(line)
    
    if not brief_added:
        # Insert overload marker after opening comment
        new_lines.insert(1, ' * @overload')
    
    return '\n'.join(new_lines)

def has_docstring(source_code: str) -> bool:
    """
    Check if the given C++ source code contains a docstring.

    Args:
        source_code: The C++ source code to check.

    Returns:
        True if the source code contains a docstring, False otherwise.
    """
    lines = source_code.split('\n')
    in_docstring = False

    for line in lines:
        line = line.strip()

        # Detect the start of a docstring
        if line.startswith('/**'):
            in_docstring = True

        # Detect the end of a docstring
        if in_docstring and line.endswith('*/'):
            return True

    return False

# Example usage in the dependency graph generation
# Assuming `source_code` is the content of a C++ file
# has_docstring_flag = has_docstring(source_code)
