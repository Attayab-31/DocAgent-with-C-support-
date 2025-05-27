"""Templates for C++ docstrings in various formats."""

DOXYGEN_FUNCTION_TEMPLATE = '''/**
 * @brief {brief}
 *
 * @details {details}
{parameters}
 * @return {returns}
 * @throws {throws}
 *
 * @code{{.cpp}}
{example}
 * @endcode
 */'''

DOXYGEN_CLASS_TEMPLATE = '''/**
 * @class {class_name}
 * @brief {brief}
 *
 * @details {details}
 *
{attributes} *
 * Example:
 * @code{.cpp}
{example}
 * @endcode
 */'''

DOXYGEN_METHOD_TEMPLATE = '''
/**
 * @brief {brief}
 * @details {details}
{parameters}
 * @return {returns}
 * @throw {throws}
 */
'''
