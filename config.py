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

# ============================================================
#  ASIAN SWEEP (ICT) STRATEGY — HỆ THỐNG 2, TÁCH BIỆT HOÀN TOÀN
# ------------------------------------------------------------
# Trade NGOÀI khung Trend Momentum (TM = 12–21h broker).
# Ý tưởng: vùng Á (04–07h) tích lũy → killzone sớm Âu (10–11h)
#   giá QUÉT biên vùng Á rồi reclaim → vào LIMIT hồi 50% cây reclaim.
#   SL sau điểm quét, TP = biên đối diện vùng Á.
# ============================================================
AS_ENABLED       = True
MAGIC_ASIAN      = 20260321
AS_COMMENT       = "AsianSweep_Bot"
AS_LOT           = 0.02

AS_RANGE_START   = 4        # Vùng Á: 04:00 (broker)
AS_RANGE_END     = 7        #        07:59 (broker)
AS_KZ_START      = 9        # Killzone vào lệnh: 09:00 (broker) — phải < TM_SESSION[0]
AS_KZ_END        = 11       #                   11:59 (broker)
AS_TP_MODE       = "range"  # "range" = biên đối diện vùng Á (hẹp) | "R" = bội số R
AS_TP_R          = 3.0      # Dùng khi AS_TP_MODE = "R"
AS_USE_BIAS      = True     # Lọc xu hướng H4: chỉ BUY khi giá > EMA(H4), SELL khi < EMA(H4)
AS_BIAS_EMA      = 50       # Chu kỳ EMA trên H4 làm bias
AS_RETRACE       = 0.5      # Hồi 50% từ điểm quét về giá đóng cây reclaim
AS_WAIT_MIN      = 60       # Chờ tối đa (phút) sau tín hiệu
AS_SL_BUF_ATR    = 0.2      # SL = điểm quét ± AS_SL_BUF_ATR × ATR(M15)
AS_HISTORY_BARS  = 5000     # Số nến M1 nạp cho chiến lược này

AS_PARTIAL_FRAC  = 0.5      # Chốt một phần (như TM)
AS_PARTIAL_AT_R  = 1.0
AS_BE_AT_R       = 1.0      # Dời SL về hòa vốn khi +1R

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
