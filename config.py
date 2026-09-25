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
TM_HISTORY_BARS = 60000      # Nến M1 nạp cho LIVE (đủ để EMA200 H1 hội tụ, sai số < 1 cent)
TM_BE_AT_R      = 1.0       # Dời SL về hòa vốn khi giá thuận lợi đạt R lần này
TM_MIN_ATR_PCT  = 0.5       # Chỉ trade khi ATR(M15) nằm ở nửa trên của 24h gần nhất
                            # (0 = tắt lọc biến động). Thử nghiệm: lọc bỏ vùng biến động thấp giúp
                            # +lợi nhuận, tăng win rate, giảm drawdown.

# --- Warmup (bỏ qua N nến đầu khi back-test để chỉ báo hội tụ) ---
# Đặt BẰNG số nến live nạp (TM_HISTORY_BARS / AS_HISTORY_BARS) để real và
# back-test dùng cùng lượng lịch sử → chỉ báo trùng khớp (< 1 cent).
TM_WARMUP_BARS  = 60000      # EMA200 H1 cần ~45 ngày nến M1 để hội tụ
AS_WARMUP_BARS  = 60000      # EMA H4(50) cần lịch sử tương đương
SMC_WARMUP_BARS = 600       # Chỉ báo ATR(EWM14) hội tụ nhanh; cần ~2 ngày nến M5 cho PDH/PDL + vùng Á

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
AS_MIN_SWEEP_ATR = 0.2      # Yêu cầu quét vượt biên Á ít nhất bội ATR(M15) này (0 = tắt)
                            # Giúp winrate 44%→65%, PF 2.44→3.44, chuỗi thua 6→3.
AS_ATR_LO        = 0.0      # Lọc biến động: chỉ trade khi ATR rank >= mức này (0 = tắt)
AS_ATR_HI        = 1.0      #                          ATR rank <= mức này (1 = tắt)
AS_HISTORY_BARS  = 60000     # Nến M1 nạp cho LIVE (đủ để EMA H4(50) hội tụ)

AS_PARTIAL_FRAC  = 0.5      # Chốt một phần (như TM)
AS_PARTIAL_AT_R  = 1.0
AS_BE_AT_R       = 1.0      # Dời SL về hòa vốn khi +1R

# ============================================================
#  SMC — SWEEP → CHoCH → OB/FVG (HỆ THỐNG 3, XAUUSD M5)
# ------------------------------------------------------------
# Mô hình SMC đầy đủ:
#   1. Trong killzone, giá QUÉT thanh khoản (PDH/PDL, biên Á, swing gần nhất).
#   2. Chờ CHoCH trên M5 (đóng nến phá swing đối diện) + displacement.
#   3. Xác định vùng vào lệnh: FVG (bắt buộc) — nếu không có thì bỏ qua setup.
#   4. Vào LIMIT tại CE (50% vùng); SL sau điểm quét; TP 3R.
#   5. Chốt 50% khối lượng tại 1R, phần còn lại chạy tới TP.
# CẤU HÌNH ĐÃ CHỐT (FINAL) — đã tích hợp bot live + backtest. Không đổi nếu
# chưa kiểm chứng lại độ ổn định trên nhiều mẫu (xem research/smc_*_stability.py).
# ============================================================
SMC_ENABLED       = True     # Đã tích hợp vào hệ thống (chọn được ở UI / backtest)
MAGIC_SMC         = 20260401
SMC_COMMENT       = "SMC_Sweep_Choch"
SMC_TF            = "M5"     # Chiến lược chạy trên khung M5 (bot tự đổi TF theo chiến lược)
SMC_LOT           = 0.02
SMC_HISTORY_BARS  = 5000

# --- Cấu trúc / thanh khoản ---
SMC_SWING_K       = 2        # Bán kính fractal xác định swing high/low (nến hai bên)
SMC_KILLZONES     = [(7, 11), (12, 16)]  # Giờ (cột time dữ liệu) được phép vào lệnh
SMC_ASIA          = (0, 6)   # Vùng Á hôm nay (0–5:59) làm mức thanh khoản
SMC_USE_ASIA_LIQ  = True     # Dùng biên vùng Á làm mức quét
SMC_USE_PDHPDL    = True     # Dùng đỉnh/đáy ngày hôm trước làm mức quét
SMC_USE_SWING_LIQ = True     # Dùng swing low/high gần nhất làm mức quét
SMC_MIN_SWEEP_ATR = 0.3      # Phải quét vượt mức ít nhất bội ATR(M5) này rồi reclaim
                             # (đã kiểm chứng: 0.3 cho kết quả bền vững hơn 0.15)
SMC_CHOCH_WAIT    = 24       # Chờ tối đa bao nhiêu nến M5 để có CHoCH sau khi quét
SMC_DISP_ATR      = 0.4      # Thân nến CHoCH tối thiểu (bội ATR) để xác nhận displacement

# --- Vùng vào lệnh (OB / FVG) ---
SMC_ZONE_LOOKBACK = 12       # Tìm FVG/OB trong bao nhiêu nến trước nến CHoCH
SMC_ENTRY_FRAC    = 0.5      # 0 = mép gần (proximal), 0.5 = CE (giữa vùng), 1 = mép xa
SMC_REQUIRE_FVG   = True     # True = bắt buộc có FVG, bỏ qua setup chỉ có OB
                             # (đã kiểm chứng: bắt buộc FVG cho kết quả tốt và ổn định hơn)
SMC_ENTRY_MODE    = "limit"  # "limit" = chờ hồi về CE (mặc định) | "market" = vào ngay khi CHoCH

# --- Quản lý lệnh ---
SMC_SL_BUF_ATR    = 0.2      # SL = điểm quét ± bội ATR(M5)
SMC_MIN_R_ATR     = 0.0      # Bỏ setup nếu SL quá hẹp (< bội ATR). 0 = tắt
SMC_MAX_R_ATR     = 6.0      # Bỏ setup nếu SL quá rộng (> bội ATR) — chặn vùng FVG dị thường.
                             # Lưu ý: R được đo theo ATR lúc CHoCH nên vẫn co giãn theo biến động.
SMC_TP_MODE       = "R"      # "R" = bội số R | "liq" = thanh khoản đối diện (PDH/PDL)
SMC_TP_R          = 3.0      # Dùng khi SMC_TP_MODE = "R"
SMC_PEND_MIN      = 120      # Số PHÚT lệnh limit chờ khớp trước khi hủy (= 24 nến M5)
SMC_ONE_PER_DAY   = True     # Tối đa 1 setup mỗi hướng mỗi ngày

# --- Partial / BE / Trailing (đã kiểm chứng độ ổn định) ---
# partial 50%@1R: PF ngang, DD giảm ~30-40%, win rate ~50% → mặc định cho SMC.
# Cần lot >= 2×volume_min (XAUUSD: >= 0.02) mới chốt một phần được.
SMC_PARTIAL_FRAC  = 0.5      # Chốt 50% khối lượng khi đạt 1R
SMC_PARTIAL_AT_R  = 1.0
SMC_BE_AT_R       = 0.0      # Dời SL hòa vốn (tắt: partial đã dời được SL sau khi chốt)
SMC_TRAIL_AT_R    = 0.0      # Trailing stop (tắt: kém ổn định trong test)
SMC_TRAIL_GAP_R   = 1.0

# ============================================================
#  HEDGING GRID (HỆ THỐNG 4, XAUUSD) — CHẠY LIÊN TỤC 24/7
# ------------------------------------------------------------
# Luật:
#   1. Mở đồng thời 1 BUY + 1 SELL. Mỗi lệnh đặt TP cách giá vào HEDGE_TP_USD.
#   2. Khi giá chạm TP của lệnh nào → broker tự đóng lệnh đó (lệnh đối diện
#      vẫn "gồng"), ĐỒNG THỜI bot mở ngay 1 cặp BUY+SELL mới tại giá hiện tại.
#   3. Mỗi lần có 1 lệnh chạm TP → mở thêm 1 cặp. Số vị thế mở tăng dần.
# KHÔNG SL (đúng mô tả): lệnh chỉ đóng khi chạm TP của chính nó → rủi ro lỗ
#   không giới hạn khi giá đi một chiều. Cân nhắc lot nhỏ.
# Bot chỉ chạy khi chiến lược đang chọn trên UI là "hedging".
# ============================================================
HEDGE_ENABLED    = True
MAGIC_HEDGE      = 20260601
HEDGE_COMMENT    = "HedgeGrid_Bot"
HEDGE_LOT        = 0.01     # Khối lượng mỗi lệnh (0.01 = nhỏ nhất)
HEDGE_TP_USD     = 3.0      # TP cách giá vào = 3.0 USD ≈ 30 pip (1 pip XAUUSD = 0.1)
HEDGE_POLL_SEC   = 0.5      # Chu kỳ bot kiểm tra TP/mở cặp mới (giây) — nhỏ để chốt sát mốc lãi
HEDGE_MAX_DEVIATION_PTS = 30  # Giới hạn trượt mỗi lệnh (points). 0 = tắt (không giới hạn)
HEDGE_OPEN_RETRIES = 3        # Số lần thử lại mỗi chân nếu sàn từ chối do trượt
HEDGE_RESET_ON_NO_MARGIN = True  # Hết margin (không mở thêm được) -> đóng toàn bộ, mở chu kỳ mới
HEDGE_LOG_BALANCE_SEC    = 0     # 0 = chỉ log khi có lệnh thoát; >0 = thêm log định kỳ mỗi N giây
# --- Chốt theo tổng lãi trong phiên (equity - đầu phiên) ---
HEDGE_TAKE_PROFIT_USD    = 1100.0  # Lãi phiên đạt mức này -> đóng toàn bộ, bắt đầu phiên mới
                                   # (để 1100 thay vì 1000 nhằm bù spread/trượt khi đóng loạt,
                                   #  thực nhận sau khi đóng ~1000$)
HEDGE_STATE_FILE         = "logs/hedge_session.json"  # Lưu mốc phiên để khởi động lại tiếp tục
HEDGE_STOP_AFTER_TARGET  = False   # True = dừng hẳn bot sau khi chốt mục tiêu
# --- Khung giờ giao dịch (GIỜ VIỆT NAM, UTC+7) ---
# Trong các khoảng skip: KHÔNG mở lệnh mới (vẫn giữ/đóng lệnh cũ theo TP).
# Lưu ý: KHÔNG còn đóng lệnh theo khung giờ; việc đóng dựa trên HEDGE_TAKE_PROFIT_USD.
HEDGE_SKIP_HOURS_VN      = [(4, 6)]
HEDGE_CLOSE_BEFORE_HOURS = 2   # (không dùng nữa — giữ lại cho tương thích)
HEDGE_TRADING_HOURS_ENABLED = True

# Chiến lược chạy mặc định khi khởi động chương trình.
# "hedging" | "trend_momentum" | "asian_sweep" | "smc"
DEFAULT_STRATEGY = "hedging"

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
