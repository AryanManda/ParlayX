"""
SQLite database storage for ParlayX.
Stores historical game data, odds, predictions, and parlay results.
"""
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime,
    Boolean, JSON, Text, ForeignKey, Index
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"

    id = Column(String, primary_key=True)  # ESPN game ID
    sport = Column(String, nullable=False, index=True)
    league = Column(String, nullable=False)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    home_score = Column(Float)
    away_score = Column(Float)
    game_date = Column(DateTime, nullable=False, index=True)
    status = Column(String, default="scheduled")  # scheduled, in_progress, final
    venue = Column(String)
    weather = Column(JSON)  # temp, wind, precipitation (outdoor sports)
    season = Column(String)
    week = Column(Integer)  # for NFL
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    odds = relationship("GameOdds", back_populates="game", cascade="all, delete-orphan")
    predictions = relationship("Prediction", back_populates="game", cascade="all, delete-orphan")


class TeamStats(Base):
    __tablename__ = "team_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sport = Column(String, nullable=False)
    team = Column(String, nullable=False)
    season = Column(String, nullable=False)
    week = Column(Integer)  # for NFL
    games_played = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)

    # Offense
    points_per_game = Column(Float)
    yards_per_game = Column(Float)      # NFL
    field_goal_pct = Column(Float)      # NBA
    three_point_pct = Column(Float)     # NBA
    batting_avg = Column(Float)         # MLB
    era = Column(Float)                 # MLB

    # Defense
    points_allowed_per_game = Column(Float)
    yards_allowed_per_game = Column(Float)  # NFL

    # Advanced
    elo_rating = Column(Float, default=1500.0)
    net_rating = Column(Float)          # NBA (off_rating - def_rating)
    pythagorean_pct = Column(Float)     # Expected win % based on points

    # Recent form (last 5/10 games)
    last5_wins = Column(Integer)
    last10_wins = Column(Integer)
    last5_ppg = Column(Float)           # Points per game last 5
    last10_ppg = Column(Float)

    home_wins = Column(Integer, default=0)
    home_losses = Column(Integer, default=0)
    away_wins = Column(Integer, default=0)
    away_losses = Column(Integer, default=0)
    rest_days = Column(Float)           # Days since last game

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_team_stats_team_season", "sport", "team", "season"),
    )


class PlayerStats(Base):
    __tablename__ = "player_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sport = Column(String, nullable=False)
    player_name = Column(String, nullable=False, index=True)
    team = Column(String, nullable=False)
    season = Column(String, nullable=False)
    game_id = Column(String, ForeignKey("games.id"))

    # Universal
    minutes_played = Column(Float)
    injured = Column(Boolean, default=False)
    injury_status = Column(String)

    # NBA
    points = Column(Float)
    rebounds = Column(Float)
    assists = Column(Float)
    steals = Column(Float)
    blocks = Column(Float)
    fg_pct = Column(Float)
    three_pct = Column(Float)
    ft_pct = Column(Float)
    turnovers = Column(Float)
    plus_minus = Column(Float)

    # NFL
    passing_yards = Column(Float)
    rushing_yards = Column(Float)
    receiving_yards = Column(Float)
    touchdowns = Column(Float)
    interceptions = Column(Float)
    receptions = Column(Float)
    targets = Column(Float)

    # MLB
    batting_avg = Column(Float)
    home_runs = Column(Float)
    rbi = Column(Float)
    strikeouts = Column(Float)
    walks = Column(Float)
    era = Column(Float)
    whip = Column(Float)
    innings_pitched = Column(Float)

    # NHL
    goals = Column(Float)
    hockey_assists = Column(Float)
    shots = Column(Float)
    save_pct = Column(Float)
    pim = Column(Float)

    # Rolling averages
    last5_avg = Column(JSON)   # {stat: avg} for last 5 games
    last10_avg = Column(JSON)  # {stat: avg} for last 10 games

    game_date = Column(DateTime, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class GameOdds(Base):
    __tablename__ = "game_odds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(String, ForeignKey("games.id"), nullable=False)
    bookmaker = Column(String, nullable=False)
    market = Column(String, nullable=False)  # h2h, spreads, totals
    outcome = Column(String, nullable=False)  # team name or over/under
    price = Column(Float, nullable=False)    # American odds
    point = Column(Float)                    # Spread or total
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    is_opening = Column(Boolean, default=False)

    game = relationship("Game", back_populates="odds")

    __table_args__ = (
        Index("ix_odds_game_market", "game_id", "market", "bookmaker"),
    )


class PlayerPropOdds(Base):
    __tablename__ = "player_prop_odds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(String, ForeignKey("games.id"))
    sport = Column(String, nullable=False)
    player_name = Column(String, nullable=False, index=True)
    team = Column(String, nullable=False)
    prop_type = Column(String, nullable=False)   # points, rebounds, passing_yards, etc.
    line = Column(Float, nullable=False)          # The prop line
    over_odds = Column(Float)
    under_odds = Column(Float)
    bookmaker = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)


class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(String, ForeignKey("games.id"), nullable=False)
    sport = Column(String, nullable=False)
    bet_type = Column(String, nullable=False)  # moneyline, spread, total, prop
    outcome = Column(String, nullable=False)
    predicted_prob = Column(Float, nullable=False)
    implied_prob = Column(Float)
    ev_pct = Column(Float)                     # Expected value %
    model_version = Column(String)
    features_used = Column(JSON)
    confidence = Column(String)                # low, medium, high
    is_ev_positive = Column(Boolean, default=False)
    actual_result = Column(Boolean)            # True=won, False=lost, None=pending
    created_at = Column(DateTime, default=datetime.utcnow)

    game = relationship("Game", back_populates="predictions")


class ParlayRecord(Base):
    __tablename__ = "parlays"

    id = Column(Integer, primary_key=True, autoincrement=True)
    legs = Column(JSON, nullable=False)         # List of prediction IDs
    leg_details = Column(JSON, nullable=False)  # Full leg info
    combined_odds = Column(Float)
    parlay_probability = Column(Float)
    ev_pct = Column(Float)
    simulated_win_pct = Column(Float)
    correlation_score = Column(Float)           # Lower is better
    recommended_stake_pct = Column(Float)       # Kelly criterion
    ai_analysis = Column(Text)                  # Claude's analysis
    ai_confidence = Column(String)
    status = Column(String, default="pending")  # pending, won, lost
    actual_result = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)


class LineMovement(Base):
    __tablename__ = "line_movements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(String, ForeignKey("games.id"))
    market = Column(String)
    outcome = Column(String)
    opening_odds = Column(Float)
    current_odds = Column(Float)
    movement = Column(Float)         # Current - opening
    sharp_money = Column(Boolean)    # True if movement suggests sharp money
    public_pct = Column(Float)       # % of public bets on this side
    timestamp = Column(DateTime, default=datetime.utcnow)


# Database engine and session factory
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, echo=False)
        Base.metadata.create_all(_engine)
    return _engine


def get_session() -> Session:
    return Session(get_engine())


def init_db():
    """Initialize the database, creating all tables."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    print(f"Database initialized at {DATABASE_URL}")
