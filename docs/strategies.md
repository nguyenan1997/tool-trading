# Chiến lược giao dịch — Tài liệu chi tiết

Trạng thái hiện tại của bot: **M1 XAUUSD**, 1 chiến lược (`trend_momentum`).
Bot chạy `trend_momentum` mặc định; đổi tham số qua giao diện/API.

---

## 1. Phương pháp Trend Momentum (`strategies/trend_momentum.py`)

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
3. **Giờ giao dịch**: `hour ∈ TM_SESSION` mặc định **(12, 21)** = phiên London + New York (giờ cột nến MT5 — xem ghi chú bên dưới).
4. **Biến động**: `ATR(14, M15)` phải nằm ở **nửa trên** (`≥ TM_MIN_ATR_PCT = 0.5`) so với 1440 nến M1 gần nhất (~24h). Bỏ qua khi thị trường êm/đi ngang. Đặt `TM_MIN_ATR_PCT = 0` để tắt.

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

### Quản lý lệnh — Chốt một phần (partial TP) + Break-even move
- **Chốt một phần**: khi giá thuận lợi đạt **`TM_PARTIAL_AT_R` = 1.0R**, bot chốt **`TM_PARTIAL_FRAC` = 50%** khối lượng (`mt5_handler.close_position_partial`, gọi từ `bot_engine._on_candle_tick`). Phần còn lại tiếp tục giữ tới TP.
  - Chỉ chốt **1 lần** cho mỗi ticket (theo dõi trong `bot_engine._partial_done`).
  - **Lưu ý lot**: cần `lot ≥ 2 × volume_min` mới chia được. XAUUSD lot min 0.01 → cần **≥ 0.02 lot**; nếu 0.01 lot thì bot tự bỏ qua (vẫn dời BE bình thường).
  - Đặt `TM_PARTIAL_FRAC = 0` để tắt.
- **BE-move**: khi giá đạt **`TM_BE_AT_R` = 1.0R**, bot **dời SL về đúng giá mở lệnh** (hòa vốn) qua `modify_position`.
- Sau khi chốt + BE: nếu giá quay về entry → phần còn lại thoát **hòa vốn** (lệnh vẫn lãi nhờ 50% đã chốt); nếu đi tiếp đến TP → phần còn lại ăn 2R.
- Không có trailing stop thêm.

> Hiệu quả đo được (100k nến M1, lot 0.01, vốn $100): chốt 50%@1R nâng win rate từ ~30% lên **~52–57%** ở cả train lẫn OOS, lợi nhuận thấp hơn đôi chút và drawdown giảm.

### Vì sao chỉ cần 20.000 nến M1?
`TM_HISTORY_BARS = 20000` (≈ 14 ngày M1) để H1 có đủ 200 nến tính EMA200/ADX chuẩn, và M15 đủ để ATR ổn định. Bot nạp **đúng số nến này mỗi lần** tính tín hiệu.

### Hiệu năng đo được (demo broker LiteFinance, XAUUSD, 06/2026 → 09/2026)
- Số lệnh: 221, win rate **~30.8%** (thắng ít nhưng payoff lớn).
- Lợi nhuận backtest 100k nến M1 với lot 0.01: **+$627** (balance 100 → 727).
- Walk-forward validate: mọi fold OOS dương, `EXP/lệnh > $1.7`.
- Nhạy cảm spread thấp (spread 0.22 → 0.40 chỉ giảm nhẹ).
- **Lưu ý**: dữ liệu broker demo ~100k nến M1 đều là trend (ADX ≥ 25) — chưa đo được hiệu quả trong thị trường đi ngang 100%.

---

## 2. Cách bot thực thi chung (`core/bot_engine.py`)

1. Bot chờ **nến M1 mới đóng** (loop theo `tf_seconds`, cộng 0.5s trễ).
2. Nạp nến: `get_candles(SYMBOL, M1, count = strategy.history_bars)` (Trend Momentum dùng 20000).
3. `strategy.calculate_indicators(df)` → nếu có vị thế đang mở: **BE-move** khi giá đạt 1R, chốt một phần khi đạt 1R (chỉ với strategy có `be_move_at_r`/`partial_at_r` > 0).
4. Nếu **không có vị thế** và `check_signal(df)` trả `BUY`/`SELL` → mở lệnh tại giá tick hiện tại (Ask/Bid), SL/TP theo `get_sl_tp`.

### Quy ước giá
- Dữ liệu nến = giá **BID**. Lệnh SELL chạm SL khi giá **Ask**(= bid + spread) tăng lên SL → trong backtest, check SL của SELL dùng `high + spread`.

### Giờ trong tài liệu
- `TM_SESSION` viết theo **giờ broker** (cột `time` của nến MT5). LiteFinance demo dùng giờ server = UTC(+2/+3 theo DST). Khi so với giờ UTC máy bạn, nhớ cộng offset (vd session 12–21 server tương ứng ~10–19 UTC mùa hè).
