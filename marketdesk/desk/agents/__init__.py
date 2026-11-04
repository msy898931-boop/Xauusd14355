"""The specialist agents. Each has one craft, its own private memory, and posts
a study to the shared blackboard. The orchestrator is the central manager."""

from .base import Agent, Study
from .correlation import CorrelationAgent
from .levels import LevelsAgent
from .momentum import MomentumAgent
from .news_agent import NewsAgent
from .risk import RiskAgent
from .structure import StructureAgent
from .surprise import SurpriseAgent
from .trend import TrendAgent
from .volatility import VolatilityAgent

# The roster, in display order. Adding an agent is one line here.
ROSTER: list[type[Agent]] = [
    StructureAgent,
    TrendAgent,
    MomentumAgent,
    LevelsAgent,
    VolatilityAgent,
    NewsAgent,
    SurpriseAgent,
    CorrelationAgent,
    RiskAgent,
]

__all__ = ["Agent", "Study", "ROSTER", "StructureAgent", "TrendAgent",
           "MomentumAgent", "LevelsAgent", "VolatilityAgent", "NewsAgent",
           "SurpriseAgent", "CorrelationAgent", "RiskAgent"]
