"""Interfaces for reading and writing data also known as input/output (I/O)
"""
# Functions and variables exposed here are part of Augur's public Python API.
# To use functions internally, import directly from the submodule.
from .file import open_file
from .metadata import read_metadata
from .sequences import read_sequences, write_sequences
