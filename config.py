# ============================================================
#  TRADING BOT CONFIGURATION
# ============================================================

# --- Symbol & Timeframe ---
SYMBOL      = "XAUUSD"
TIMEFRAME   = "M1"          # M1 M5 M15 M30 H1 H4 D1

# ============================================================
#  ICT — KILLZONE → LIQUIDITY SWEEP → DISPLACEMENT/FVG (XAUUSD M5)
# ------------------------------------------------------------
# Mô hình ICT "Power of 3 / Silver Bullet" (Michael J. Huddleston):
#   1. Bias khung lớn (prevday/h4ema) — chỉ trade cùng chiều bias.
#   2. Vùng tích lũy Á (00–06h broker) tạo thanh khoản ở hai biên.
#   3. Trong killzone (London 07–11h, NY 12–16h) giá QUÉT biên ĐỐI DIỆN bias
#      (manipulation) rồi đóng nến reclaim.
#   4. Displacement + CHoCH để lại FVG → vào LIMIT tại CE/OTE.
#   5. SL sau điểm quét; TP = DRAW ON LIQUIDITY (PDH/PDL hoặc biên Á đối diện).
#   6. Chốt 50% @1R, phần còn lại chạy tới DOL.
# Đã kiểm chứng độ ổn định qua research/ict.py (train/OOS + walk-forward).
# ============================================================
ICT_ENABLED       = True
MAGIC_ICT         = 20260927
ICT_COMMENT       = "ICT_KZ_Sweep_FVG"
ICT_TF            = "M5"
ICT_LOT           = 0.02
ICT_HISTORY_BARS  = 5000
ICT_WARMUP_BARS   = 600

# --- Cấu trúc / thời gian ---
ICT_SWING_K       = 2        # Bán kính fractal xác định swing high/low
ICT_KILLZONES     = [(7, 20)]  # Giờ broker: London + New York (7–19h59)
ICT_ASIA          = (0, 6)   # Vùng tích lũy Á (0–5:59 giờ broker)

# --- Bias khung lớn (chỉ trade cùng chiều) ---
# Đã kiểm chứng: "prevday" (bias = hướng nến ngày hôm trước) bền vững nhất
# (train PF 3.12 · OOS 4.51 · walk-forward mọi fold dương). "none" = nhiều lệnh hơn.
ICT_BIAS_MODE     = "prevday"  # "prevday" | "h4ema" | "none"
ICT_BIAS_EMA      = 50       # Chu kỳ EMA H4 khi bias_mode = "h4ema"

# --- Mức thanh khoản bị quét ---
ICT_USE_ASIA_LIQ  = True     # Biên vùng Á hôm nay
ICT_USE_PDHPDL    = True     # Đỉnh/đáy ngày hôm trước
ICT_USE_SWING_LIQ = True     # Swing low/high gần nhất
ICT_MIN_SWEEP_ATR = 0.3      # Phải quét vượt mức ≥ bội ATR(M5) này rồi reclaim

# --- Displacement / vùng vào lệnh ---
ICT_CHOCH_WAIT    = 36       # Chờ tối đa bao nhiêu nến M5 để có CHoCH sau khi quét (36 nến = 3h)
ICT_DISP_ATR      = 0.4      # Thân nến CHoCH tối thiểu (bội ATR) để xác nhận displacement
ICT_ZONE_LOOKBACK = 12       # Tìm FVG trong bao nhiêu nến trước nến CHoCH
ICT_REQUIRE_FVG   = True     # True = bắt buộc có FVG, bỏ qua setup không có FVG
ICT_ENTRY_FRAC    = 0.5      # 0 = mép gần, 0.5 = CE, ~0.62–0.79 = OTE (điểm vào sâu hơn)
ICT_ENTRY_MODE    = "limit"  # "limit" = chờ hồi về vùng | "market" = vào ngay khi CHoCH

# --- Ẩn SL/TP khỏi sàn: chỉ bot biết, tự cắt bằng MARKET khi chạm điểm ---
# True = KHÔNG gửi SL/TP lên broker (sàn không thấy); bot theo dõi nội bộ và
# đóng lệnh bằng market khi giá chạm SL/TP (BUY theo BID, SELL theo ASK = bid+spread).
ICT_MANUAL_SLTP      = True
MANUAL_SLTP_POLL_SEC = 1      # chu kỳ kiểm tra SL/TP (giây)
MANUAL_SLTP_FILE     = "logs/manual_sltp.json"  # lưu SL/TP để tiếp quản khi restart

# --- Quản lý rủi ro / TP ---
ICT_SL_BUF_ATR    = 0.2      # SL = điểm quét ± bội ATR(M5)
ICT_MIN_R_ATR     = 0.0      # Bỏ setup nếu SL quá hẹp (< bội ATR). 0 = tắt
ICT_MAX_R_ATR     = 6.0      # Bỏ setup nếu SL quá rộng (> bội ATR)
# Đã kiểm chứng: TP theo DOL (draw on liquidity) thắng TP bội số R cho PP này.
ICT_TP_MODE       = "liq"    # "liq" = draw on liquidity | "R" = bội số R
ICT_TP_R          = 3.0      # Dùng khi ICT_TP_MODE = "R"
ICT_TP_MIN_R      = 0.0      # TP theo DOL phải cách entry ≥ bội R này (0 = lấy mức gần nhất)
ICT_PEND_MIN      = 120      # Số PHÚT lệnh limit chờ khớp trước khi hủy (= 24 nến M5)
ICT_ONE_PER_DAY   = True     # Tối đa 1 setup mỗi hướng mỗi ngày

# --- Chốt lời từng phần ---
ICT_PARTIAL_FRAC  = 0.0      # Chốt một phần (0 = tắt; kiểm chứng: tắt cho RR/PF tốt hơn)
ICT_PARTIAL_AT_R  = 1.0
ICT_BE_AT_R       = 0.0      # Dời SL hòa vốn (tắt mặc định)

# --- Bộ lọc FVG nâng cao (học từ "Ranked FVG Imbalance Zones" – Zeiierman) ---
ICT_MIN_FVG_ATR    = 0.3     # Bỏ FVG nhỏ hơn k×ATR (0 = tắt). 0.3 tốt nhất trên 90k nến M5.
ICT_VOL_MA         = 20      # Chu kỳ MA volume
ICT_VOL_MIN        = 0.0     # Nến displacement phải có volume ≥ k×volMA (0 = tắt)
ICT_SKIP_MITIGATED = False   # Bỏ FVG đã bị lấp ≥ ICT_MITIGATE_MAX
ICT_MITIGATE_MAX   = 0.5     # Ngưỡng lấp (0..1) coi như FVG hết giá trị
ICT_FVG_SELECT     = "near"  # "near" = FVG gần nhất | "score" = điểm chất lượng cao nhất

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
HEDGE_TP_USD     = 5.0      # TP cách giá vào = 5.0 USD ≈ 50 pip (1 pip XAUUSD = 0.1)
HEDGE_POLL_SEC   = 0.5      # Chu kỳ bot kiểm tra TP/mở cặp mới (giây) — nhỏ để chốt sát mốc lãi
HEDGE_MAX_DEVIATION_PTS = 30  # Giới hạn trượt mỗi lệnh (points). 0 = tắt (không giới hạn)
HEDGE_OPEN_RETRIES = 3        # Số lần thử lại mỗi chân nếu sàn từ chối do trượt
HEDGE_RESET_ON_NO_MARGIN = True  # Hết margin (không mở thêm được) -> đóng toàn bộ, mở chu kỳ mới
HEDGE_LOG_BALANCE_SEC    = 0     # 0 = chỉ log khi có lệnh thoát; >0 = thêm log định kỳ mỗi N giây
# --- Đóng phiên khi số BUY ≈ số SELL (từ lệnh thứ N trở đi) ---
HEDGE_BALANCE_MIN_ORDERS = 400   # Từ số lệnh này trong phiên mới xét cân bằng (0 = tắt)
HEDGE_BALANCE_PCT        = 0.05  # |BUY−SELL| ≤ PCT×max(BUY,SELL) → coi là cân bằng → đóng phiên
# --- Chốt theo tổng lãi trong phiên (equity - đầu phiên) ---
HEDGE_TAKE_PROFIT_USD    = 1100.0  # Lãi phiên đạt mức này -> đóng toàn bộ, bắt đầu phiên mới
                                   # (để 1100 thay vì 1000 nhằm bù spread/trượt khi đóng loạt,
                                   #  thực nhận sau khi đóng ~1000$)
HEDGE_STATE_FILE         = "logs/hedge_session.json"  # Lưu mốc phiên để khởi động lại tiếp tục
HEDGE_STOP_AFTER_TARGET  = False   # True = dừng hẳn bot sau khi chốt mục tiêu
# --- Khung giờ giao dịch (GIỜ VIỆT NAM, UTC+7) ---
# ĐÃ TẮT: bot giao dịch 24/7, KHÔNG chặn khung giờ nào.
HEDGE_SKIP_HOURS_VN      = []
HEDGE_CLOSE_BEFORE_HOURS = 2   # (không dùng)
HEDGE_TRADING_HOURS_ENABLED = False

# Chiến lược chạy mặc định khi khởi động chương trình.
# "hedging" | "ict"
DEFAULT_STRATEGY = "hedging"

# ============================================================
#  GUARD — chặn/đóng lệnh KHÔNG do bot mở
# ------------------------------------------------------------
# Khi bot đang chạy, định kỳ quét toàn bộ vị thế/lệnh chờ. Lệnh nào có
# `magic` KHÔNG thuộc các chiến lược của bot (vd mở tay, từ app mobile,
# từ EA khác) => GHI LOG cảnh báo và ĐÓNG/HỦY ngay.
# ============================================================
GUARD_EXTERNAL       = True   # Bật bộ guard
GUARD_POLL_SEC       = 2      # Chu kỳ quét (giây)
GUARD_SYMBOL_ONLY    = True   # True = chỉ quét symbol bot (config.SYMBOL); False = mọi symbol
GUARD_CLOSE_EXTERNAL = True   # True = đóng/hủy ngay; False = chỉ log

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
