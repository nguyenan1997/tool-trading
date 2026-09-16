# ============================================================
#  TRADING BOT CONFIGURATION
# ============================================================

# --- Symbol & Timeframe ---
SYMBOL      = "XAUUSD"
TIMEFRAME   = "M1"          # M1 M5 M15 M30 H1 H4 D1

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
TM_MIN_ATR_PCT  = 0.5       # Chỉ trade khi ATR(M15) nằm ở nửa trên của 24h gần nhất
                            # (0 = tắt lọc biến động). Thử nghiệm: lọc bỏ vùng biến động thấp giúp
                            # +lợi nhuận, tăng win rate, giảm drawdown.

# --- Partial Take-Profit (chốt lời từng phần) ---
# Khi giá đạt TM_PARTIAL_AT_R lần khoảng cách SL, chốt TM_PARTIAL_FRAC khối lượng.
# Phần còn lại tiếp tục chạy tới TP; SL được dời về hòa vốn sau khi chốt.
# Đặt TM_PARTIAL_FRAC = 0 để tắt. LƯU Ý: cần lot >= 2 × volume_min mới chia được
# (XAUUSD lot min 0.01 → cần >= 0.02 lot; 0.01 lot sẽ tự bỏ qua).
TM_PARTIAL_FRAC = 0.5       # Tỷ lệ khối lượng chốt sớm (0.5 = 50%)
TM_PARTIAL_AT_R = 1.0       # Chốt khi giá đạt R lần này

# --- Lot Size Mode ---
# "FIXED"  → always use FIXED_LOT
# "RISK"   → calculate lot based on RISK_PERCENT of balance
LOT_MODE        = "FIXED"
FIXED_LOT       = 0.02      # >= 0.02 để chốt một phần (partial TP) hoạt động
RISK_PERCENT    = 10.0      # % of account balance per trade

# --- Order Settings ---
# Trượt giá tối đa khi vào lệnh (points; XAUUSD 1 point = 0.01).
# Nếu giá khớp lệch quá ngưỡng này so với giá yêu cầu -> hủy/đóng ngay, coi như không vào lệnh.
# Đặt 0 để tắt giới hạn.
MAX_SLIPPAGE_POINTS = 50

# MAGIC riêng để quản lý lệnh độc lập (giữ nguyên SL/TP khi đổi tham số).
MAGIC_TM        = 20260320   # Trend Momentum
ORDER_COMMENT   = "TrendMomentum_Bot"

# --- Trading Hours (UTC) – leave empty to trade 24/7 ---
# Example: TRADE_HOURS = [(0, 22)]  means trade from 00:00 to 22:00 UTC
TRADE_HOURS     = []        # [] = no restriction

# --- Backtest ---
BACKTEST_CACHE_HOURS = 0.1  # Cache cũ hơn bao nhiêu giờ thì tự tải lại (0.1 ≈ 6 phút; đặt 0 = luôn tải lại)

# --- Hiển thị giờ trên UI ---
VN_UTC_OFFSET     = 7       # Múi giờ Việt Nam (UTC+7) để hiển thị
BROKER_UTC_OFFSET = 0       # Dự phòng khi chưa đọc được giờ broker từ MT5 (0 = coi là UTC)

# --- Logging ---
LOG_FILE = "logs/bot.log"
LOG_LEVEL       = "INFO"    # DEBUG / INFO / WARNING / ERROR
