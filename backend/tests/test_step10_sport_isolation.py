import unittest
from backend.engine.football_elo_engine import run_football_elo_engine
from backend.engine.football_poisson_engine import run_football_poisson_engine
from backend.engine.basketball_elo_engine import run_basketball_elo_engine
from backend.engine.basketball_poisson_engine import run_basketball_poisson_engine
from backend.engine.baseball_elo_engine import run_baseball_elo_engine
from backend.engine.baseball_poisson_engine import run_baseball_poisson_engine
from backend.engine.hockey_elo_engine import run_hockey_elo_engine
from backend.engine.hockey_poisson_engine import run_hockey_poisson_engine
from backend.engine.glicko2_engine import run_glicko2_engine
from backend.engine.gradient_boosting_engine import run_gradient_boosting_engine

class TestSportIsolation(unittest.TestCase):
    def test_football_elo_rejects_other_sports(self):
        """Verify that Football ELO model rejects basketball, baseball, and hockey sports"""
        with self.assertRaises(ValueError):
            run_football_elo_engine("basketball", "Lakers", "Warriors", "2026-10-01T00:00:00Z")
        with self.assertRaises(ValueError):
            run_football_elo_engine("baseball", "Yankees", "Red Sox", "2026-10-01T00:00:00Z")
        with self.assertRaises(ValueError):
            run_football_elo_engine("hockey", "Bruins", "Blackhawks", "2026-10-01T00:00:00Z")

    def test_football_poisson_rejects_other_sports(self):
        """Verify that Football Poisson model rejects other sports"""
        with self.assertRaises(ValueError):
            run_football_poisson_engine("basketball", "Lakers", "Warriors", "2026-10-01T00:00:00Z")

    def test_basketball_elo_rejects_other_sports(self):
        """Verify that Basketball ELO model rejects other sports"""
        with self.assertRaises(ValueError):
            run_basketball_elo_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")

    def test_basketball_poisson_rejects_other_sports(self):
        """Verify that Basketball Poisson model rejects other sports"""
        with self.assertRaises(ValueError):
            run_basketball_poisson_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")

    def test_baseball_elo_rejects_other_sports(self):
        """Verify that Baseball ELO model rejects other sports"""
        with self.assertRaises(ValueError):
            run_baseball_elo_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")

    def test_baseball_poisson_rejects_other_sports(self):
        """Verify that Baseball Poisson model rejects other sports"""
        with self.assertRaises(ValueError):
            run_baseball_poisson_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")

    def test_hockey_elo_rejects_other_sports(self):
        """Verify that Hockey ELO model rejects other sports"""
        with self.assertRaises(ValueError):
            run_hockey_elo_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")

    def test_hockey_poisson_rejects_other_sports(self):
        """Verify that Hockey Poisson model rejects other sports"""
        with self.assertRaises(ValueError):
            run_hockey_poisson_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")

    def test_glicko2_rejects_unsupported_sports(self):
        """Verify Glicko2 model rejects unsupported sports"""
        with self.assertRaises(ValueError):
            run_glicko2_engine("unsupported_sport", "Team A", "Team B", "2026-10-01T00:00:00Z")

    def test_gradient_boosting_rejects_unsupported_sports(self):
        """Verify Gradient Boosting model rejects unsupported sports"""
        with self.assertRaises(ValueError):
            run_gradient_boosting_engine("unsupported_sport", "Team A", "Team B", "2026-10-01T00:00:00Z", {})

if __name__ == "__main__":
    unittest.main()
