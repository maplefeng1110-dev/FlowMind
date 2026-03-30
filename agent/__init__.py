"""Agent package.

Avoid loading agent/.env as an import side effect so Agent-owned utilities can
be reused by other processes without implicitly mutating their environment.
"""
