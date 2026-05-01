"""sys.path setup for modify_agent tests.

Helpers live in helpers.py so the project-root conftest doesn't shadow them.
"""
import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, '..', '..', 'build_agent')))
sys.path.insert(0, _HERE)  # so `from helpers import ...` works
