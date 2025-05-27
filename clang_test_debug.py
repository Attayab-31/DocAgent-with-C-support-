import os
import logging
import clang.cindex
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def test_clang_setup():
    """Test Clang initialization and C++ file parsing."""
    # Try to find and configure libclang
    if os.path.exists(r"C:\Program Files\LLVM\bin\libclang.dll"):
        logger.info("Found libclang.dll in Program Files")
        clang.cindex.Config.set_library_file(r"C:\Program Files\LLVM\bin\libclang.dll")
    else:
        logger.error("Could not find libclang.dll")
        return False
    
    # Test file path
    test_file = Path("data/testrepo/subtract.cpp")
    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return False
    
    try:
        # Create index and parse file
        index = clang.cindex.Index.create()
        logger.info("Successfully created clang Index")
        
        tu = index.parse(str(test_file.absolute()))
        logger.info("Successfully parsed translation unit")
        
        # Print diagnostics
        for diag in tu.diagnostics:
            logger.info(f"Diagnostic: {diag.spelling} (severity: {diag.severity})")
        
        # Try to extract some basic information
        cursor = tu.cursor
        logger.info(f"File: {cursor.spelling}")
        
        # Walk the AST and look for functions and classes
        def visit_node(node):
            if node.kind == clang.cindex.CursorKind.FUNCTION_DECL:
                logger.info(f"Found function: {node.spelling}")
            elif node.kind == clang.cindex.CursorKind.CLASS_DECL:
                logger.info(f"Found class: {node.spelling}")
            elif node.kind == clang.cindex.CursorKind.CXX_METHOD:
                logger.info(f"Found method: {node.spelling}")
            
            for child in node.get_children():
                visit_node(child)
        
        visit_node(cursor)
        return True
        
    except Exception as e:
        logger.error(f"Error in Clang processing: {e}")
        return False

if __name__ == "__main__":
    success = test_clang_setup()
    print(f"\nClang test {'succeeded' if success else 'failed'}")
