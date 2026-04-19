"""
Mutable runtime state set once at startup.
Not suitable for multi-process deployments — fine for single-container setup.
"""

master_bot_username: str = ""
