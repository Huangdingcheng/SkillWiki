"""SkillWiki - A Skill-Centric Operating System for Self-Evolving Agents"""

__version__ = "0.1.0"

from .config import ConfigManager, get_config_manager, reset_config_manager
from .utils import get_logger

__all__ = [
    "ConfigManager",
    "get_config_manager",
    "reset_config_manager",
    "get_logger",
]
