"""MarketDesk — a multi-agent market & news analysis desk.

The orchestrator is the central manager: it gathers candles and news into the
shared memory, wakes each specialist agent, collects their studies, and issues
the final recommendation. Every number carries its provenance; a value that
cannot be computed is reported as None, never as 0.
"""

__version__ = "0.1.0"
