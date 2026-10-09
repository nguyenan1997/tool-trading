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
# Đã tối ưu (real-tick): thu hẹp về đúng London (7-10h59) + NY (12-15h59) → PF 1.19→1.62.
ICT_KILLZONES     = [(7, 11), (12, 16)]  # Giờ broker: London open + New York open
ICT_ASIA          = (0, 6)   # Vùng tích lũy Á (0–5:59 giờ broker)

# --- Bias khung lớn (chỉ trade cùng chiều) ---
# Tối ưu lại trên mô hình realistic + MT5 real-tick: "h4ema" (giá vs EMA50 H4)
# vượt trội hơn "prevday" (MT5: PF 1.19→2.22, DD 1.96%→0.72%). "none" = nhiều lệnh hơn.
ICT_BIAS_MODE     = "h4ema"   # "prevday" | "h4ema" | "none"
ICT_BIAS_EMA      = 50       # Chu kỳ EMA H4 khi bias_mode = "h4ema"

# --- Mức thanh khoản bị quét ---
ICT_USE_ASIA_LIQ  = True     # Biên vùng Á hôm nay
ICT_USE_PDHPDL    = True     # Đỉnh/đáy ngày hôm trước
ICT_USE_SWING_LIQ = True     # Swing low/high gần nhất
ICT_MIN_SWEEP_ATR = 0.3      # Phải quét vượt mức ≥ bội ATR(M5) này rồi reclaim

# --- Displacement / vùng vào lệnh ---
ICT_CHOCH_WAIT    = 36       # Chờ tối đa bao nhiêu nến M5 để có CHoCH sau khi quét (36 nến = 3h)
ICT_DISP_ATR      = 0.5      # Thân nến CHoCH tối thiểu (bội ATR) để xác nhận displacement (tối ưu: 0.4→0.5)
ICT_ZONE_LOOKBACK = 12       # Tìm FVG trong bao nhiêu nến trước nến CHoCH
ICT_REQUIRE_FVG   = True     # True = bắt buộc có FVG, bỏ qua setup không có FVG
ICT_ENTRY_FRAC    = 0.62     # 0 = mép gần, 0.5 = CE, ~0.62–0.79 = OTE (tối ưu: 0.5→0.62)
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
ICT_PARTIAL_FRAC  = 0.5      # Chốt một phần @1R (MT5 real-tick 1 năm: DD equity 20.5%->12.4%, WR 53%->63%, net -24%)
ICT_PARTIAL_AT_R  = 1.0
ICT_BE_AT_R       = 0.0      # Dời SL hòa vốn (tắt mặc định)

# --- Bộ lọc FVG nâng cao (học từ "Ranked FVG Imbalance Zones" – Zeiierman) ---
ICT_MIN_FVG_ATR    = 0.3     # Bỏ FVG nhỏ hơn k×ATR (0 = tắt). 0.3 tốt nhất trên 90k nến M5.
ICT_VOL_MA         = 20      # Chu kỳ MA volume
ICT_VOL_MIN        = 0.0     # Nến displacement phải có volume ≥ k×volMA (0 = tắt)
ICT_SKIP_MITIGATED = False   # Bỏ FVG đã bị lấp ≥ ICT_MITIGATE_MAX
ICT_MITIGATE_MAX   = 0.5     # Ngưỡng lấp (0..1) coi như FVG hết giá trị
ICT_FVG_SELECT     = "near"  # "near" = FVG gần nhất | "score" = điểm chất lượng cao nhất

# --- ICT nâng cấp (chuẩn ICT) ---
ICT_USE_WEEKLY_LIQ  = False    # PWH/PWL (đỉnh/đáy tuần trước) làm mức thanh khoản + DOL
ICT_USE_MIDNIGHT    = False    # Midnight open (giá mở ngày) làm mức thanh khoản + DOL
ICT_NEWS_SKIP_TIMES = []       # Bỏ vào lệnh quanh tin: ["14:30","20:00"] (giờ BROKER)
ICT_NEWS_SKIP_MIN   = 45       # Bỏ +/- bao nhiêu phút quanh giờ tin
ICT_MIN_CONFLUENCE  = 0        # Số PD array tối thiểu trùng nhau tại điểm quét (0 = tắt)

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

# ============================================================
#  SMART GOLD HUNTER (XAUUSD) — SINGLE ENTRY / ONE SHOT
# ------------------------------------------------------------
# Phỏng theo EA "Smart Gold Hunter" (MQL5 product 170050):
#   No Grid / No Martingale / No Recovery / No Hedging — Single Entry with SL.
#   Mỗi setup = 1 lệnh duy nhất, có SL/TP thật + Break Even + trailing.
#   Nhiều profile (Striker mặc định / Scalper / Swinger / Prop / PRR / Custom)
#   + lớp bảo vệ: giới hạn lãi/lỗ ngày, spread, news, đóng lệnh thứ 6.
# ============================================================
SGH_ENABLED  = True
MAGIC_SGH    = 20260605
SGH_COMMENT  = "SmartGoldHunter"
SGH_TF       = "M15"
SGH_LOT      = 0.02
SGH_HISTORY_BARS = 3000
SGH_WARMUP_BARS  = 200
SGH_PROFILE  = "PRR Scalping"   # Striker | Ultimate Scalper | Swinger | Prop Scalper | PRR Scalping | Custom

# --- Entry (breakout Donchian + xác nhận) ---
SGH_BREAK_LOOKBACK = 20      # Số nến cho đỉnh/đáy phá vỡ
SGH_BREAK_BUF_ATR  = 0.1     # Buffer phá vỡ (bội ATR)
SGH_MIN_BODY_ATR   = 0.3     # Thân nến tối thiểu (bội ATR)
SGH_TREND_MODE     = "ema"   # "none" | "ema"
SGH_EMA            = 50
SGH_SL_ATR         = 1.5     # SL = entry ∓ bội ATR
SGH_TP_R           = 1.5
SGH_MAX_TRADES_PER_DAY = 3
SGH_MIN_BARS_BETWEEN   = 3   # Tối thiểu số nến giữa 2 lệnh

# --- Quản lý lệnh (áp dụng cả backtest + live) ---
SGH_PARTIAL_FRAC = 0.0       # 0 = không chốt một phần
SGH_PARTIAL_AT_R = 1.0
SGH_BE_AT_R      = 1.0       # Dời SL về hòa vốn
SGH_TRAIL_AT_R   = 1.0       # Bắt đầu trailing sau X R
SGH_TRAIL_GAP_R  = 0.8       # SL bám cách giá tốt nhất Y R

# --- Lớp bảo vệ ---
SGH_MAX_SPREAD_POINTS = 40   # Không vào lệnh nếu spread > mức này (points). 0 = tắt
SGH_FRIDAY_CLOSE_HOUR = 21   # >= giờ này thứ 6 -> không vào lệnh (25 = tắt)
SGH_NEWS_FILTER   = True
SGH_NEWS_HOUR     = 12       # Tin mạnh ~12:30 (xấp xỉ)
SGH_NEWS_MINUTE   = 30
SGH_NEWS_SKIP_MIN = 45
SGH_NEWS_TIMES    = []       # Thêm giờ tin "HH:MM" ngoài NFP, vd ["14:00","15:30"]
SGH_DAILY_PROFIT_TARGET_PCT = 0.0   # Chạm mục tiêu lãi ngày (%) -> ngừng vào lệnh. 0 = tắt
SGH_DAILY_LOSS_LIMIT_PCT    = 3.0   # Lỗ ngày vượt mức (%) -> ngừng vào lệnh. 0 = tắt
SGH_EQUITY_PROTECTION_PCT   = 5.0   # Equity giảm quá X% từ đỉnh ngày -> ngừng vào lệnh. 0 = tắt
# --- Kiểu SL / randomizer (giống bản gốc) ---
SGH_SL_FROM_EXEC = True      # True = SL/TP tính từ GIÁ KHỚP thực tế
SGH_HIDE_INITIAL_SL = False  # True = ẩn SL ban đầu khỏi sàn (bot tự cắt nội bộ)
SGH_ENTRY_RANDOM_POINTS = 0  # Làm lệch SL/TP ngẫu nhiên ±N points (0 = tắt)

# Chiến lược chạy mặc định khi khởi động chương trình.
# "hedging" | "sgh" | "ict"  (phải chọn PP trên UI rồi bấm Start mới chạy)
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

# --- Backtest realism (mô phỏng sát thực tế) ---
BACKTEST_EXEC_TF            = "M1"   # Nến nhỏ hơn để xác định THỨ TỰ chạm SL/TP trong nến ("" = tắt, dùng OHLC nến tín hiệu)
BACKTEST_REALISTIC_FILLS    = True   # Nến khớp lệnh chờ không tính TP trong cùng nến (SL trước)
BACKTEST_COMMISSION_PER_LOT = 0.0    # Phí mỗi lot MỖI CHIỀU (USD). Vd sàn thu $3.5/lot/lần => 3.5
BACKTEST_SLIPPAGE_POINTS    = 0      # Trượt giá bất lợi (points; XAUUSD 1 point = 0.01)
BACKTEST_SPREAD_MULT        = 1.0    # Nhân spread thật của nến (mô phỏng spread dãn lúc biến động)
BACKTEST_SPREAD_MIN         = 0.0    # Sàn spread (price). Vd 0.30

# --- Hiển thị giờ trên UI ---
VN_UTC_OFFSET     = 7       # Múi giờ Việt Nam (UTC+7) để hiển thị
BROKER_UTC_OFFSET = 0       # Dự phòng khi chưa đọc được giờ broker từ MT5 (0 = coi là UTC)

# --- Logging ---
LOG_FILE = "logs/bot.log"
LOG_LEVEL       = "INFO"    # DEBUG / INFO / WARNING / ERROR
