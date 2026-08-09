"""Shared singletons wired together at import time.

Keeping them in one module avoids circular imports between routers and
services while staying easily replaceable in tests.
"""

from __future__ import annotations

from app.game import GameService
from app.leaderboard import LeaderboardService
from app.ws_manager import ConnectionManager

manager = ConnectionManager()
game = GameService(manager)
leaderboard = LeaderboardService()