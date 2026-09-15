# ============================================================
#  TRADING BOT CONFIGURATION
# ============================================================

# --- Symbol & Timeframe ---
SYMBOL      = "XAUUSD"
TIMEFRAME   = "M1"          # M1 M5 M15 M30 H1 H4 D1

# --- Indicator Settings ---
EMA_FAST        = 9
EMA_MEDIUM      = 21
EMA_SLOW        = 50

# --- Strategy Settings ---
RR_RATIO        = 3.0       # Tỷ lệ Risk:Reward (1:3)

# --- Trend Momentum Strategy ---
# Điểm vào: EMA200 H1 (trend) + ADX H1 > ngưỡng + Donchian (phá đỉnh/đáy 20 nến M1)
#           + RSI M1 cùng chiều, chỉ trade trong phiên London+New York (UTC).
# Thoát: SL = TM_SL_ATR × ATR(M15), TP = TM_TP_R × khoảng cách SL; BE-move khi +1R.
TM_LOOKBACK     = 20        # Số nến M1 cho Donchian breakout
TM_RSI_PERIOD   = 7
TM_RSI_BUY      = 55
TM_RSI_SELL     = 45
TM_SL_ATR       = 1.5       # SL = TM_SL_ATR × ATR(M15)
TM_TP_R         = 2.0       # TP = TM_TP_R × SL distance (2R)
TM_ADX_THRESH   = 22        # Chỉ trade khi ADX H1 ≥ ngưỡng
TM_SESSION      = (12, 21)  # (Giờ bắt đầu, giờ kết thúc) UTC — London + New York
TM_HISTORY_BARS = 20000     # Số nến M1 cần nạp để tính chỉ báo H1/M15
TM_BE_AT_R      = 1.0       # Dời SL về hòa vốn khi giá thuận lợi đạt R lần này

# --- Lot Size Mode ---
# "FIXED"  → always use FIXED_LOT
# "RISK"   → calculate lot based on RISK_PERCENT of balance
LOT_MODE        = "FIXED"
FIXED_LOT       = 0.01
RISK_PERCENT    = 10.0      # % of account balance per trade

# --- Order Settings ---
MAGIC_NUMBER    = 20260319
ORDER_COMMENT   = "EMA_Crossover_Bot"

# --- Trading Hours (UTC) – leave empty to trade 24/7 ---
# Example: TRADE_HOURS = [(0, 22)]  means trade from 00:00 to 22:00 UTC
TRADE_HOURS     = []        # [] = no restriction

# --- Logging ---
LOG_FILE = "logs/bot.log"
LOG_LEVEL       = "INFO"    # DEBUG / INFO / WARNING / ERROR
