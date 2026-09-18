# Chiến lược giao dịch — Tài liệu chi tiết

Trạng thái hiện tại của bot: **XAUUSD**, 3 chiến lược (`trend_momentum`, `asian_sweep`, `smc`).
Bot chạy chiến lược đang chọn trên UI/API (các PP loại trừ nhau); đổi tham số qua giao diện/API.

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

1. Bot chờ **nến mới đóng** của khung theo chiến lược đang chọn (`M1`; riêng SMC là `M5`).
2. Nạp nến: `get_candles(SYMBOL, strategy.timeframe, count = strategy.history_bars)`.
3. `strategy.calculate_indicators(df)` → nếu có vị thế đang mở: **BE-move** khi giá đạt 1R, chốt một phần khi đạt 1R (chỉ với strategy có `be_move_at_r`/`partial_at_r` > 0).
4. Nếu **không có vị thế** và `check_signal(df)` trả `BUY`/`SELL` → mở lệnh tại giá tick hiện tại (Ask/Bid), SL/TP theo `get_sl_tp`.
5. Với chiến lược dùng **lệnh chờ limit** (`get_pending_setup`), bot đặt mức chờ và khớp khi giá hồi tới `level`; thời gian chờ tính theo **phút** (`wait_min`).

### Quy ước giá
- Dữ liệu nến = giá **BID**. Lệnh SELL chạm SL khi giá **Ask**(= bid + spread) tăng lên SL → trong backtest, check SL của SELL dùng `high + spread`.

### Giờ trong tài liệu
- `TM_SESSION` viết theo **giờ broker** (cột `time` của nến MT5). LiteFinance demo dùng giờ server = UTC(+2/+3 theo DST). Khi so với giờ UTC máy bạn, nhớ cộng offset (vd session 12–21 server tương ứng ~10–19 UTC mùa hè).

---

## 3. Phương pháp SMC — Sweep → CHoCH → OB/FVG (`strategies/smc.py`)

Chiến lược Smart Money Concept chạy trên **XAUUSD M5**, dùng lệnh **LIMIT**.

### Quy trình
1. **Quét thanh khoản** trong killzone (mặc định 07–11h và 12–16h giờ broker):
   giá thâm nhập qua một mức — biên vùng Á hôm nay, PDH/PDL ngày trước, hoặc
   swing low/high gần nhất — vượt ít nhất `SMC_MIN_SWEEP_ATR`×ATR(M5), rồi
   **đóng cửa trở lại** trên mức (reclaim).
2. **CHoCH**: trong tối đa `SMC_CHOCH_WAIT` nến, nến M5 đóng phá swing đối
   diện kèm **displacement** (thân nến ≥ `SMC_DISP_ATR`×ATR).
3. **Vùng vào lệnh**: **FVG** mới nhất trong `SMC_ZONE_LOOKBACK` nến trước
   CHoCH; nếu không có (và `SMC_REQUIRE_FVG` = False) → **Order Block**.
4. **Vào LIMIT** tại `SMC_ENTRY_FRAC` của vùng (0 = mép gần, 0.5 = CE);
   **SL** sau điểm quét ± `SMC_SL_BUF_ATR`×ATR; **TP** = `SMC_TP_R`×R.
5. Tối đa 1 setup mỗi hướng mỗi ngày; lệnh chờ hủy sau `SMC_PEND_MIN` phút.

### Cách chốt lời (mặc định: partial 50%@1R)
- Chốt **50% khối lượng tại 1R** (`SMC_PARTIAL_FRAC = 0.5`, `SMC_PARTIAL_AT_R = 1.0`),
  phần còn lại chạy tới **TP 3R** (`SMC_TP_R = 3.0`). Không trailing, không dời BE.
- Yêu cầu **lot ≥ 0.02** (XAUUSD volume_min 0.01) mới chia được khối lượng.
- Đã kiểm chứng ổn định (3 mẫu × 4 fold):
  partial 50%@1R cho PF ngang/cao hơn, **DD giảm ~30–40%**, **WR ~50%**;
  trailing và TP-theo-thanh-khoản kém ổn định nên không dùng.

### Kết quả backtest tham khảo (XAUUSD M5, vốn $1000, lot 0.02, partial 50%@1R, 2026-02→09)
- 101 lệnh · Win rate **49.5%** · PF **2.17** · Expectancy **+$9.15/lệnh** · Max DD **6.1%**.
- OOS (30% cuối) PF 2.18; walk-forward 4 fold: worst **1.04**, mean **2.20**.
- So với TP 3R thuần: PF 2.10, DD 9.7%, net $1222 → partial đổi ~25% lãi lấy DD thấp và WR cao.

### Hệ thống backtest
- **API**: `POST /api/backtest/run` (tham số `strategy=smc`) → trả về
  `profit_factor`, `expectancy`, `max_drawdown`, `avg_win/avg_loss`,
  `timeframe`… SMC tự ép chạy khung **M5**.
- **UI**: trang `/backtest` có thẻ chọn SMC và khối tham số riêng.
- **CLI**: `python run_backtest.py smc [count]`.
- **Nghiên cứu chuyên sâu**: `python research/smc.py` (train/OOS + walk-forward
  + tách đoạn dữ liệu liên tục).

### Lưu ý
- `SMC_ENABLED = True`: đã tích hợp vào hệ thống (chọn được trên UI/live).
- Chỉ chạy trong killzone; ngoài phiên bot không vào lệnh mới nhưng vẫn quản lý
  vị thế đang mở của chính nó.
- Mẫu backtest còn nhỏ (~100 lệnh) và lợi nhuận phụ thuộc vài lệnh thắng lớn —
  nên forward-test trên demo trước khi tăng vốn.

### Cách vào lệnh (`SMC_ENTRY_MODE`)
`limit` (mặc định) = chờ hồi về CE của FVG/OB; `market` = vào ngay khi CHoCH.
Đã kiểm chứng độ ổn định (3 mẫu × 4 fold):
`market` nhiều lệnh hơn nhưng PF thấp hơn nhiều (~1.28 vs ~2.0) và drawdown
gấp ~3 lần (~18–20% vs ~6%); `limit` vẫn tốt hơn rõ rệt nên GIỮ mặc định.

---

## 4. Cấu hình CHỐT (FINAL) — SMC Sweep→CHoCH→FVG

| Tham số | Giá trị | Ghi chú |
|---|---|---|
| Khung / sản phẩm | M5 · XAUUSD | bot tự chọn M5 khi bật SMC |
| Killzone | 07–11h, 12–16h (broker) | `SMC_KILLZONES` |
| Swing K | 2 | `SMC_SWING_K` |
| Quét tối thiểu | 0.3 × ATR(M5) | `SMC_MIN_SWEEP_ATR` |
| Chờ CHoCH | 24 nến | `SMC_CHOCH_WAIT` |
| Displacement | 0.4 × ATR | `SMC_DISP_ATR` |
| Vùng vào lệnh | **FVG bắt buộc** | `SMC_REQUIRE_FVG = True` |
| Tìm vùng | 12 nến | `SMC_ZONE_LOOKBACK` |
| Cách vào | **LIMIT tại CE (0.5)** | `SMC_ENTRY_MODE = "limit"` |
| Chờ khớp | 120 phút | `SMC_PEND_MIN` |
| SL | sau điểm quét ± 0.2×ATR | `SMC_SL_BUF_ATR` |
| R tối đa | 6 × ATR | `SMC_MAX_R_ATR` |
| TP | 3R | `SMC_TP_MODE = "R"`, `SMC_TP_R = 3.0` |
| Chốt lời | **50% @1R** + phần còn lại tới 3R | `SMC_PARTIAL_FRAC/AT_R` |
| BE / Trailing | tắt | `SMC_BE_AT_R = 0`, `SMC_TRAIL_AT_R = 0` |
| Số setup | 1/hướng/ngày | `SMC_ONE_PER_DAY` |

**Kết quả chốt (XAUUSD M5, lot 0.02, vốn $1000):**
- Full 2026-02→09 (101 lệnh): WR **49.5%**, PF **2.17**, Expectancy **+$9.15/lệnh**,
  DD **6.1%**, OOS PF 2.18.
- Mẫu 150k (90 lệnh): WR 51.1%, PF 2.05, DD 7.2%.
- Walk-forward 4 fold: worst **1.04**, mean **2.20**.

Trạng thái: đã tích hợp live (chọn được trên UI) + backtest API/UI/CLI. Chỉ đổi
tham số sau khi kiểm chứng lại độ ổn định trên nhiều mẫu.
