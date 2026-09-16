# Chiến lược giao dịch — Tài liệu chi tiết

Trạng thái hiện tại của bot: **M1 XAUUSD**, 2 chiến lược (`3ema`, `trend_momentum`).
Mặc định bot chạy `3ema`. User đổi qua giao diện/API bằng `strategy_id`.

---

## 1. Phương pháp 3 EMA Crossover (`strategies/triple_ema.py`)

### Ý tưởng
Bắt kịp xu hướng khi EMA nhanh cắt EMA vừa, và khẳng định xu hướng bằng việc giá đóng nến nằm trên/dưới cả 3 EMA (trend filter).

### Khung thời gian
- Chỉ dùng nến **M1** (khung của bot). Không có multi-timeframe.

### Chỉ báo
| Chỉ báo | Tham số | Mặc định |
|---|---|---|
| EMA nhanh | `EMA_FAST` | 9 |
| EMA vừa | `EMA_MEDIUM` | 21 |
| EMA chậm | `EMA_SLOW` | 50 |

### Điều kiện vào lệnh (kiểm tra trên **nến đóng** `df.iloc[-2]`)
Tín hiệu chỉ được xét sau khi **nến thứ N đã đóng** (bot chờ nến kế tiếp mới tính tín hiệu).

**LỆNH MUA (BUY)** — tất cả điều kiện cùng xảy ra:
1. **Crossover**: EMA9 cắt lên trên EMA21 tại nến hiện tại, trong khi nến trước đó EMA9 ≤ EMA21.
   `cross_up = (ema9_prev ≤ ema21_prev) and (ema9_now > ema21_now)`
2. **Xác nhận xu hướng tăng**: giá đóng nến `close > EMA9` AND `close > EMA21` AND `close > EMA50`.

**LỆNH BÁN (SELL)** — đối xứng:
1. **Crossdown**: EMA9 cắt xuống dưới EMA21 tại nến hiện tại, nến trước EMA9 ≥ EMA21.
   `cross_down = (ema9_prev ≥ ema21_prev) and (ema9_now < ema21_now)`
2. **Xác nhận xu hướng giảm**: `close < EMA9` AND `close < EMA21` AND `close < EMA50`.

**Không có filter về giờ giao dịch** (trade 24/7, trừ khi bật `TRADE_HOURS` ở config).

### Stop Loss / Take Profit
- **SL** = giá của **EMA21** tại nến tín hiệu (`ema21` tại `iloc[-2]`).
- **TP** = dựa theo tỷ lệ Risk:Reward (`RR_RATIO = 3.0`, tức 1:3):
  - BUY: khoảng cách `dist = entry - SL`; `TP = entry + dist × RR`.
  - SELL: `dist = SL - entry`; `TP = entry - dist × RR`.
- Nếu `dist ≤ 0` (SL không hợp lệ), **bỏ qua lệnh** (không vào).

### Quản lý lệnh
Ra vào chuẩn market/limit ở tick tiếp theo: giá vào = giá Ask (BUY) hoặc Bid (SELL) tại lúc xử lý.
SL/TP đặt ngay khi mở lệnh. **Không có** trailing / break-even.

### Đánh giá
- Win rate cao hơn (~30-40%) nhưng lợi nhuận thực tế kém hơn Trend Momentum (đã kiểm tra backtest: dễ bị SL quét nhiều trong sideway).

---

## 2. Phương pháp Trend Momentum (`strategies/trend_momentum.py`)

### Ý tưởng
Chỉ vào lệnh khi **xu hướng lớn H1 rõ ràng** (ADX tăng + EMA200), và vào lệnh tại **điểm phá vỡ Momentum trên M1** (Donchian breakout 20 nến) có xác nhận RSI cùng chiều. Chỉ trade trong **phiên London + New York**.

### Khung thời gian (multi-timeframe)
| Khung | Dữ liệu | Công dụng |
|---|---|---|
| H1 | EMA200, ADX(14) | Trend filter (xu hướng lớn) |
| M15 | ATR(14) | Định kích thước SL (volatility) |
| M1 | Donchian 20, RSI(7) | Xác định điểm vào lệnh |

Toàn bộ chỉ báo dùng nến **ĐÃ ĐÓNG** (shift 1 trên mỗi khung), không nhìn vào nến hiện tại.

### Điều kiện vào lệnh (kiểm tra trên **nến M1 đóng** `df.iloc[-2]`)
**Tiền lọc (buffers) – nếu thoả tất cả:**
1. **Trend H1**: giá đóng nến phải nằm đúng phía so với EMA200 H1 (BUY: `close > ema200`; SELL: `close < ema200`).
2. **Sức mạnh xu hướng H1**: `ADX(14) H1 ≥ TM_ADX_THRESH` (mặc định **22**).
3. **Giờ giao dịch**: `hour ∈ TM_SESSION` mặc định **(12, 21)** = phiên London + New York (giờ UTC; giờ cột nến MT5 — xem ghi chú bên dưới).

**LỆNH MUA (BUY):**
- `close > EMA200(H1)` — xu hướng lớn tăng.
- `close > prior_high` — **nến M1 đóng phá đỉnh 20 nến trước** (Donchian breakout; `prior_high = max(high, 20 nến, dịch 1)`).
- `RSI(7) M1 ≥ TM_RSI_BUY` (mặc định **55**) — momentum còn khoẻ.

**LỆNH BÁN (SELL):** đối xứng.
- `close < EMA200(H1)`.
- `close < prior_low` — phá đáy 20 nến trước.
- `RSI(7) M1 ≤ TM_RSI_SELL` (mặc định **45**).

> Nếu bất kỳ giá trị chỉ báo nào là NaN (thời gian đầu chưa đủ dữ liệu) → **không có tín hiệu** (an toàn).

### Stop Loss / Take Profit
- **Khoảng cách SL** = `TM_SL_ATR × ATR(14, M15)` (mặc định **1.5 × ATR15**).
  - BUY: `SL = entry - dist`; SELL: `SL = entry + dist`.
- **TP** = `TM_TP_R × dist` (mặc định **2.0**, tức 2R).
  - BUY: `TP = entry + dist × 2`; SELL: `TP = entry - dist × 2`.
- Nếu `ATR15` không hợp lệ (`NaN`/≤0) → **bỏ qua lệnh**.

### Quản lý lệnh — Break-even move
- Bot (không phải chiến lược) quản lý **BE-move**: khi giá thuận lợi đạt **`TM_BE_AT_R` = 1.0R** (tức lãi ≥ 1 lần khoảng cách SL ban đầu), bot **dời SL về đúng giá mở lệnh** (hòa vốn) qua `modify_position` (live) hoặc trong loop backtest (`engine._check_exit`).
- Sau khi BE: nếu giá quay về entry → thoát **hòa vốn** (+0), nếu đi tiếp đến TP → +2R.
- Không có trailing stop thêm.

### Vì sao chỉ cần 20.000 nến M1?
`TM_HISTORY_BARS = 20000` (≈ 14 ngày M1) để H1 có đủ 200 nến tính EMA200/ADX chuẩn, và M15 đủ để ATR ổn định. Bot nạp **đúng số nến này mỗi lần** tính tín hiệu (khác với 3EMA chỉ cần vài trăm nến).

### Hiệu năng đo được (demo broker LiteFinance, XAUUSD, 06/2026 → 09/2026)
- Số lệnh: 221, win rate **~30.8%** (thắng ít nhưng payoff lớn).
- Lợi nhuận backtest 100k nến M1 với lot 0.01: **+$627** (balance 100 → 727).
- Walk-forward validate: mọi fold OOS dương, `EXP/lệnh > $1.7`.
- Nhạy cảm spread thấp (spread 0.22 → 0.40 chỉ giảm nhẹ).
- **Lưu ý**: dữ liệu broker demo ~100k nến M1 đều là trend (ADX ≥ 25) — chưa đo được hiệu quả trong thị trường đi ngang 100%.

---

## 3. Cách bot thực thi chung (`core/bot_engine.py`)

1. Bot chờ **nến M1 mới đóng** (loop theo `tf_seconds`, cộng 0.5s trễ).
2. Nạp nến: `get_candles(SYMBOL, M1, count = strategy.history_bars)` (3EMA dùng mặc định cũ, Trend Momentum dùng 20000).
3. `strategy.calculate_indicators(df)` → nếu có vị thế đang mở: **BE-move** khi giá đạt 1R (chỉ với strategy có `be_move_at_r > 0`).
4. Nếu **không có vị thế** và `check_signal(df)` trả `BUY`/`SELL` → mở lệnh tại giá tick hiện tại (Ask/Bid), SL/TP theo `get_sl_tp`.

### Quy ước giá
- Dữ liệu nến = giá **BID**. Lệnh SELL chạm SL khi giá **Ask**(= bid + spread) tăng lên SL → trong backtest, check SL của SELL dùng `high + spread`.

### Giờ trong tài liệu
- `TM_SESSION` viết theo **giờ broker** (cột `time` của nến MT5). LiteFinance demo dùng giờ server = UTC(+2/+3 theo DST). Khi so với giờ UTC máy bạn, nhớ cộng offset (vd session 12–21 server tương ứng ~10–19 UTC mùa hè).

---

## 4. So sánh nhanh

| Tiêu chí | 3 EMA Crossover | Trend Momentum |
|---|---|---|
| TF | M1 | M1 + M15 + H1 |
| Trend filter | Giá vs 3 EMA M1 | EMA200 H1 + ADX H1 ≥ 22 |
| Entry | EMA9×21 cross + giá >/< cả 3 EMA | Donchian 20-nến breakout + RSI(7) cùng chiều |
| Giờ giao dịch | 24/7 | Phiên (12, 21) server |
| SL | EMA21 | 1.5 × ATR(M15) |
| TP | 3 × SL | 2 × SL |
| BE-move | Không | Có (khi +1R) |
| Số nến cần nạp | nhỏ (~200) | 20000 |
| Phong cách | Nhiều lệnh, win rate cao | Ít lệnh, payoff cao, lợi nhuận ròng tốt hơn |