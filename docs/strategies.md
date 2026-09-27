# Chiến lược giao dịch — Tài liệu chi tiết

Trạng thái hiện tại của bot: **XAUUSD**, 2 chiến lược (`hedging`, `ict`).
Bot chạy chiến lược đang chọn trên UI/API (các PP loại trừ nhau); đổi tham số qua giao diện/API.

---

## 1. Phương pháp ICT — Killzone → Sweep → Displacement/FVG (`strategies/ict.py`)

PP lấy cảm hứng từ ICT (Power of 3, liquidity sweep, FVG, killzone, draw on
liquidity). Chạy **XAUUSD M5**, dùng lệnh **LIMIT**. Chi tiết đầy đủ + checklist thủ
công: **`docs/ict_playbook.md`**.

### Quy trình
1. **Bias (`ICT_BIAS_MODE`)**: mặc định `prevday` = hướng nến ngày hôm trước → chỉ
   trade cùng chiều bias.
2. **Killzone** (giờ broker): London **07–11h**, NY **12–16h**.
3. **Quét thanh khoản** biên ĐỐI DIỆN bias (PDH/PDL, biên vùng Á, swing gần nhất),
   vượt ≥ `ICT_MIN_SWEEP_ATR`×ATR rồi **reclaim** (đóng cửa trở lại).
4. **Displacement + CHoCH** trong `ICT_CHOCH_WAIT` nến (thân nến ≥ `ICT_DISP_ATR`×ATR).
5. **FVG bắt buộc** trong `ICT_ZONE_LOOKBACK` nến trước CHoCH.
6. **LIMIT** tại CE (`ICT_ENTRY_FRAC`=0.5; ~0.62–0.79 = OTE).
7. **TP = draw on liquidity**: mức thanh khoản đối diện gần nhất (fallback bội số R).
8. **Chốt 50% @1R**, phần còn lại tới DOL.

### Kết quả (XAUUSD M5, lot 0.02, vốn $1000, 2026-02→09, spread 0.22)
- Toàn bộ (34 lệnh): WR **79.4%**, PF **3.57**, net +$254, DD **4.0%**.
- Train 70% (21 lệnh): PF 3.12 · OOS 30% (13 lệnh): PF **4.51**, DD 2.0%.
- Walk-forward 4 fold: PF 1.65 / 4.71 / 9.98 / 3.74 — mọi fold dương.
- Đặc điểm: win rate cao + RR < 1 (thắng nhiều, ăn ít mỗi lệnh).

### Cách chạy
- Live/UI: chọn "ICT KZ→Sweep→FVG" (`strategy_manager` key = `ict`).
- CLI: `python run_backtest.py ict [count]` — Nghiên cứu: `python research/ict.py`.

### Lưu ý
- Mẫu nhỏ (34 lệnh) → forward-test demo trước khi tăng vốn; RR < 1 nhạy spread.

---

## 2. Cách bot thực thi chung (`core/bot_engine.py`)

1. Bot chờ **nến mới đóng** của khung theo chiến lược đang chọn (`M1`; riêng ICT là `M5`).
2. Nạp nến: `get_candles(SYMBOL, strategy.timeframe, count = strategy.history_bars)`.
3. `strategy.calculate_indicators(df)` → nếu có vị thế đang mở: **BE-move** khi giá đạt 1R, chốt một phần khi đạt 1R (chỉ với strategy có `be_move_at_r`/`partial_at_r` > 0).
4. Nếu **không có vị thế** và `check_signal(df)` trả `BUY`/`SELL` → mở lệnh tại giá tick hiện tại (Ask/Bid), SL/TP theo `get_sl_tp`.
5. Với chiến lược dùng lệnh chờ (`get_pending_setup`), bot đặt **lệnh LIMIT THẬT trên MT5**
   (`buy_limit`/`sell_limit`) tại mức CE, kèm SL/TP và thời gian hết hạn → sàn tự khớp
   trong nến (khớp đúng như backtest). BUY đặt tại `level + spread` (khớp theo ask),
   SELL đặt tại `level` (khớp theo bid). Lệnh quá hạn/killzone bị hủy (broker + bot).

### Quy ước giá
- Dữ liệu nến = giá **BID**. Lệnh SELL chạm SL khi giá **Ask**(= bid + spread) tăng lên SL → trong backtest, check SL của SELL dùng `high + spread`.

### Giờ trong tài liệu
- Giờ killzone viết theo **giờ broker** (cột `time` của nến MT5). LiteFinance demo dùng giờ server = UTC(+2/+3 theo DST). Khi so với giờ UTC máy bạn, nhớ cộng offset.
