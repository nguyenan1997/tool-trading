# ============================================================
#  TRADING BOT CONFIGURATION
# ============================================================

# --- Symbol & Timeframe ---
SYMBOL      = "XAUUSD"
TIMEFRAME   = "M5"          # M1 M5 M15 M30 H1 H4 D1

# --- Indicator Settings ---
ST_PERIOD       = 10
ST_MULTIPLIER   = 3.0
EMA_PERIOD      = 100

# --- Lot Size Mode ---
# "FIXED"  → always use FIXED_LOT
# "RISK"   → calculate lot based on RISK_PERCENT of balance
LOT_MODE        = "FIXED"
FIXED_LOT       = 0.1
RISK_PERCENT    = 10.0      # % of account balance per trade

# --- Order Settings ---
MAGIC_NUMBER    = 20260317
ORDER_COMMENT   = "ST_EMA_Bot"

# --- Trading Hours (UTC) – leave empty to trade 24/7 ---
# Example: TRADE_HOURS = [(0, 22)]  means trade from 00:00 to 22:00 UTC
TRADE_HOURS     = []        # [] = no restriction

# --- Logging ---
LOG_FILE        = "bot.log"
LOG_LEVEL       = "INFO"    # DEBUG / INFO / WARNING / ERROR
