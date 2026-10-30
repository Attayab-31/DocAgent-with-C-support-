# Copyright (c) Meta Platforms, Inc. and affiliates
from setuptools import setup, find_packages

# Read the contents of README file
from pathlib import Path
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

# Prepare all extras
dev_requires = [
    "pytest>=8.3.4",
    "pytest-cov>=2.0",
    "black>=22.0",
    "flake8>=3.9",
]

web_requires = [
    "flask>=3.1.0",
    "flask-socketio>=5.5.1",
    "eventlet>=0.39.0",
    "python-socketio>=5.12.1",
    "python-engineio>=4.11.2",
    "bidict>=0.23.0",
    "dnspython>=2.7.0",
    "six>=1.16.0",
]

visualization_requires = [
    "matplotlib>=3.10.0",
    "pygraphviz>=1.14",
    "networkx>=3.4.2",
]

cuda_requires = [
    "torch>=2.0.0",
    "accelerate>=1.4.0",
]

# Combine all extras for the 'all' option
all_requires = dev_requires + web_requires + visualization_requires + cuda_requires

setup(
    name="DocstringGenerator",
    version="0.1.0",
    author="Dayu Yang",
    author_email="dayuyang@meta.com",
    description="DocAgent for High-quality docstring generation in Large-scale Python projects",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
    ],
    python_requires=">=3.10",
    install_requires=[
        # Core dependencies
        "numpy>=1.23.5",
        "pyyaml>=6.0",
        "jinja2>=3.1.5",
        "requests>=2.32.0",
        "urllib3>=2.3.0",
        
        # Code analysis tools
        "astor>=0.8.1",
        "code2flow>=2.5.1",
        "pydeps>=3.0.0",
        
        # AI/LLM related dependencies
        "anthropic>=0.45.0",
        "openai>=1.60.1",
        "langchain-anthropic>=0.3.4",
        "langchain-openai>=0.3.2",
        "langchain-core>=0.3.31",
        "langgraph>=0.2.67",
        "tiktoken>=0.8.0",
        "transformers>=4.48.0",
        "huggingface-hub>=0.28.0",
        "google-generativeai>=0.6.0",
        
        # Utility packages
        "tqdm>=4.67.1",
        "tabulate>=0.9.0",
        "colorama>=0.4.6",
        "termcolor>=2.5.0",
        "pydantic>=2.10.0",

        # Web requirements 
        "flask>=3.1.0",
        "flask-socketio>=5.5.1",
        "eventlet>=0.39.0",
        "python-socketio>=5.12.1",
        "python-engineio>=4.11.2",
        "bidict>=0.23.0",
        "dnspython>=2.7.0",
        "six>=1.16.0",

        # CUDA requirements 
        "torch>=2.0.0",
        "accelerate>=1.4.0",
    ],
    extras_require={
        "dev": dev_requires,
        "web": web_requires,  # Keep for potential compatibility, now included in core
        "visualization": visualization_requires,
        "cuda": cuda_requires, # Keep for potential compatibility, now included in core
        "all": all_requires,
    }
)
