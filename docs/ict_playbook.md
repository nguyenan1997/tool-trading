# Playbook ICT — Killzone → Liquidity Sweep → Displacement/FVG

Phương pháp giao dịch lấy cảm hứng từ **ICT (Inner Circle Trader – Michael J. Huddleston)**,
tổng hợp từ mô hình *Power of 3*, *liquidity sweep*, *fair value gap*, *killzone*,
*displacement*, *optimal trade entry* và *draw on liquidity*. Đây là **PP thứ 5** của bot,
mã hoá trong `strategies/ict.py` (chạy XAUUSD M5).

> Mục tiêu của PP: **không đuổi giá**. Chờ "cá lớn" quét thanh khoản (manipulation),
> xác nhận ý định bằng cấu trúc + FVG, rồi vào lệnh theo hướng dòng tiền lớn với
> TP ở vùng thanh khoản kế tiếp.

---

## 1. Triết lý — Power of 3 (AMD)

Mỗi chu kỳ giao dịch của dòng tiền lớn gồm 3 pha:

| Pha | Tiếng Anh | Biểu hiện trên chart | Hành động của trader ICT |
|---|---|---|---|
| Tích lũy | **Accumulation** | Đi ngang, biên độ hẹp (thường là vùng Á) | Xác định 2 biên = 2 bể thanh khoản |
| Thao túng | **Manipulation** | Cú đẩy mạnh phá 1 biên để quét stop-loss rồi quay đầu | **Chờ** cú quét, không vào sớm |
| Phân phối | **Distribution** | Di chuyển mạnh đúng hướng, để lại FVG | Vào lệnh tại FVG, nhắm thanh khoản đối diện |

Giá **đi từ vùng thanh khoản này sang vùng thanh khoản khác** → luôn phải biết
"nam châm" thanh khoản kế tiếp (draw on liquidity).

---

## 2. Thuật ngữ cốt lõi

- **Bể thanh khoản (Liquidity pool):** nơi tụ stop-loss/lệnh chờ — đỉnh/đáy cũ,
  đỉnh/đáy bằng nhau (EQH/EQL), PDH/PDL, biên vùng Á.
- **Quét thanh khoản (Liquidity sweep / stop hunt):** giá phá nhanh qua mức để lấy
  thanh khoản rồi **đóng cửa trở lại** (reclaim) → dấu hiệu manipulation.
- **FVG (Fair Value Gap):** khoảng trống giữa 3 nến do nến giữa (displacement) đi quá
  nhanh. Bullish FVG = `low[nến3] > high[nến1]`. Là vùng mất cân bằng → thường được
  giá hồi về lấp (một phần) trước khi tiếp diễn.
- **Displacement:** nến thân lớn, động lượng mạnh — bằng chứng có dòng lệnh thật.
- **CHoCH / MSS (Change of Character / Market Structure Shift):** nến đóng phá swing
  đối diện → xác nhận đảo cấu trúc sau cú quét.
- **Killzone:** khung giờ tổ chức hoạt động mạnh (London open, New York open).
- **OTE (Optimal Trade Entry):** vùng hồi 0.62–0.79 của cây đẩy → điểm vào "đẹp".
- **Draw on liquidity (DOL):** vùng thanh khoản đối diện mà giá đang bị hút tới → TP.

---

## 3. Quy trình 7 bước (mô hình đầy đủ)

```
Bước 0  Bias            H4/D1 + nến ngày trước  → chỉ trade 1 hướng
Bước 1  Killzone        London + NY 07–20h (giờ broker)
Bước 2  Vùng tích lũy   Vùng Á 00–06h → lấy asia_hi / asia_lo
Bước 3  Quét            Trong killzone: giá quét biên ĐỐI DIỆN bias + reclaim
Bước 4  Xác nhận        Displacement + CHoCH (nến đóng phá swing đối diện)
Bước 5  Vùng vào lệnh   FVG mới nhất trước CHoCH (bắt buộc)
Bước 6  Vào lệnh        LIMIT tại CE (0.5) hoặc OTE (~0.7) của FVG
Bước 7  Thoát           SL sau điểm quét; TP = draw on liquidity đối diện
```

### Bước 0 — Bias (chỉ giao dịch theo 1 hướng)
- **`prevday`** (mặc định, đã kiểm chứng): nếu nến ngày hôm trước **tăng** → hôm nay
  chỉ tìm lệnh BUY; **giảm** → chỉ SELL.
- `h4ema`: so giá với EMA50 H4. `none`: bỏ lọc bias (nhiều lệnh hơn, PF thấp hơn).
- Với bias tăng, thao túng là cú **quét xuống** (lấy sell-side liquidity) rồi phân phối lên.

### Bước 1 — Killzone (giờ broker)
- **07:00–19:59** giờ broker (mặc định `ICT_KILLZONES = [(7,20)]` — bắt trọn London + New York).
  *(Kiểm chứng trên 90k nến M5: mở rộng tới 20h cho PF & net tốt hơn khung 7–11/12–16.)*
- Ngoài killzone: không vào lệnh mới (vẫn quản lý lệnh đang mở).

### Bước 2 — Vùng tích lũy Á
- Biên vùng Á (00:00–05:59) tạo 2 bể thanh khoản hai bên → nguồn bị quét.

### Bước 3 — Điều kiện quét (manipulation)
Giá quét một mức thanh khoản (một trong các mức bật):
`asia_lo/hi` hôm nay · `PDL/PDH` · swing low/high gần nhất, **vượt ≥ 0.3×ATR(M5)**
rồi **đóng nến reclaim** trở lại phía trong.
- BUY: `low < mức − 0.3×ATR` và `close > mức`.
- SELL: đối xứng.

### Bước 4 — Displacement + CHoCH
Trong tối đa **24 nến M5** (2h) sau cú quét: có nến đóng **phá swing đối diện** với
**thân nến ≥ 0.4×ATR** (displacement). Đây chính là "xác nhận" của ICT — cấu trúc +
displacement, **không dùng mẫu nến xác nhận riêng**.

### Bước 5 — FVG (vùng vào lệnh bắt buộc) + lọc chất lượng
- Tìm FVG mới nhất trong **12 nến** trước nến CHoCH. Không có FVG → **bỏ setup**.
- **Lọc kích thước:** chỉ nhận FVG có **kích thước ≥ `ICT_MIN_FVG_ATR`×ATR** (mặc định **0.3**).
  FVG quá nhỏ (nhiễu) bị loại → chỉ giữ "imbalance" thật. *(Trên 90k nến M5: PF 2.63 → 4.81, DD 6% → 2.8%.)*
- FVG chính là "dấu vết" của dòng lệnh lớn; giá thường hồi về lấp một phần.

### Bước 6 — Vào lệnh
- **LIMIT** tại **CE (0.5)** của FVG (hoặc `ICT_ENTRY_FRAC ≈ 0.62–0.79` cho kiểu OTE).
- Lệnh chờ **hết hạn sau 120 phút** (24 nến M5) nếu chưa khớp.
- Tối đa **1 setup mỗi hướng mỗi ngày**.

### Bước 7 — SL & TP
- **SL** = sau điểm quét (đáy/đỉnh cực trị) −/+ `0.2×ATR(M5)`.
- Chặn setup nếu SL quá rộng (> 6×ATR) để tránh vùng FVG dị thường.
- **TP = draw on liquidity:** chọn mức thanh khoản **gần nhất** phía đối diện trong
  {PDH/PDL, biên Á đối diện, swing đối diện}. Không có mức hợp lệ → TP theo bội số R.

---

## 4. Quản lý lệnh

- **Không chốt một phần** (tắt `ICT_PARTIAL_FRAC = 0`) — giữ nguyên tới TP.
  *(Kiểm chứng trên 90k nến M5: bỏ partial cho RR 1.38→1.45, PF 4.68→4.91, DD giữ nguyên.)*
- Phần còn lại chạy tới TP (DOL). Không trailing, không dời BE mặc định.
- Cần **lot ≥ 0.02** (XAUUSD volume_min 0.01) mới chia được khối lượng.

---

## 5. Bảng cấu hình CHỐT (FINAL) — `config.py`

| Tham số | Giá trị | Ý nghĩa |
|---|---|---|
| Khung / sản phẩm | M5 · XAUUSD | bot tự chọn M5 |
| Killzone | (7,20) | giờ broker |
| Vùng Á | (0,6) | nguồn thanh khoản |
| Bias | **prevday** | hướng nến ngày trước |
| Swing K | 2 | fractal |
| Độ sâu quét tối thiểu | 0.3 × ATR | `ICT_MIN_SWEEP_ATR` |
| Chờ CHoCH | 24 nến | `ICT_CHOCH_WAIT` |
| Displacement | 0.4 × ATR | `ICT_DISP_ATR` |
| Vùng vào lệnh | FVG bắt buộc | `ICT_REQUIRE_FVG = True` |
| Tìm vùng | 12 nến | `ICT_ZONE_LOOKBACK` |
| Lọc FVG size | ≥ 0.3 × ATR | `ICT_MIN_FVG_ATR = 0.3` |
| Vào tại | CE (0.5) | `ICT_ENTRY_FRAC` |
| Chế độ vào | LIMIT | `ICT_ENTRY_MODE = "limit"` |
| SL | sau quét ± 0.2×ATR | `ICT_SL_BUF_ATR` |
| R tối đa | 6 × ATR | `ICT_MAX_R_ATR` |
| TP | **DOL** (mức gần nhất) | `ICT_TP_MODE = "liq"`, `ICT_TP_MIN_R = 0` |
| Chốt lời | Giữ tới TP (không partial) | `ICT_PARTIAL_FRAC = 0` |
| Số setup | 1/hướng/ngày | `ICT_ONE_PER_DAY` |

---

## 6. Kết quả kiểm chứng (XAUUSD M5, lot 0.02, vốn $1000)

Dữ liệu gộp M1→M5: **2026-02-23 → 2026-09-18** (38.515 nến M5), spread 0.22.

Cấu hình chốt = bias `prevday` + TP DOL + **lọc FVG size ≥ 0.3×ATR** (bỏ các bộ lọc
nến xác nhận / Premium-Discount / volume vì không bền vững).

Dữ liệu **90.000 nến M5** (2025-06 → 2026-09, ~15 tháng):

| Cấu hình | n | Win rate | PF | Net | Max DD |
|---|---|---|---|---|---|
| baseline (tắt lọc) | 88 | 77.3% | 2.63 | +$457 | 6.0% |
| **size≥0.3 (chốt)** | 74 | 78.4% | **4.81** | +$568 | 2.8% |
| size≥0.5 | 67 | 74.6% | 3.49 | +$553 | 7.6% |

- Lọc **size≥0.3** cải thiện **khiêm tốn nhưng ổn** (PF 2.63 → 4.81, DD 6% → 2.8%).
- Ngưỡng 0.5/0.7 **không rõ ràng hơn** → **0.3** là mức hợp lý.
- Đặc trưng PP: **ít lệnh**, PF khá, RR < 1 (phụ thuộc win rate cao).

> **Cảnh báo về độ tin cậy:** các kết quả PF rất cao trước đây là do **mẫu nhỏ**.
> Trên 90k nến, mức cải thiện **khiêm tốn hơn nhiều** — đừng kỳ vọng PF 20.
> Nên forward-test demo trước khi tin.
> Khi tự kiểm chứng trên dữ liệu broker lớn hơn, kết quả có thể khác.

### Điều gì tạo ra lợi thế?
Kiểm chứng tách biến (train / OOS PF; toàn bộ trên cùng dữ liệu):

| Cấu hình | Train PF | OOS PF | Ghi chú |
|---|---|---|---|
| **bias prevday + TP DOL** | 3.12 | 4.51 | **CHỐT** |
| bias none + TP DOL | 2.34 | 3.42 | bias giúp tăng PF |
| bias prevday + TP 3R | 1.52 | 1.00 | TP DOL > TP bội số R |
| bias prevday + TP 2R | 1.68 | 1.32 | |

→ Hai yếu tố thực sự quan trọng: **bias** (chỉ trade cùng chiều) và **TP theo draw on
liquidity** (chốt tại bể thanh khoản gần nhất thay vì bội số R cố định).
Bộ lọc "nến xác nhận" và "Premium/Discount" đã được thử và **loại bỏ** (không bền vững).

---

## 7. Checklist giao dịch thủ công

```
[ ] 1. Xác định bias hôm nay (nến ngày trước tăng/giảm) → chỉ trade 1 hướng
[ ] 2. Đang trong killzone? (07–20h giờ broker — London + NY)
[ ] 3. Đánh dấu thanh khoản: PDH/PDL, biên vùng Á, đỉnh/đáy gần nhất
[ ] 4. Chờ giá QUÉT biên đối diện bias ≥ 0.3×ATR rồi ĐÓNG CỬA reclaim
[ ] 5. Chờ displacement + CHoCH (nến đóng phá swing đối diện, thân ≥ 0.4×ATR)
[ ] 6. Có FVG trong 12 nến trước CHoCH? Nếu không → BỎ QUA
[ ] 7. Đặt LIMIT tại CE (hoặc OTE 0.62–0.79) của FVG
[ ] 8. SL sau điểm quét ± 0.2×ATR; TP = bể thanh khoản đối diện gần nhất
[ ] 9. Giữ nguyên tới TP (không chốt một phần)
[ ] 10. Hết ngày / ngoài killzone → không vào lệnh mới
```

---

## 8. Cách chạy trong dự án

- **Live/bot:** chọn PP "ICT KZ→Sweep→FVG" trên UI (`strategy_manager` key = `ict`).
  Lệnh dùng **LIMIT thật trên MT5**, tự hủy khi quá hạn.
- **Backtest UI:** trang `/backtest`, thẻ **ICT KZ → Sweep → FVG** (tự ép khung M5).
- **CLI:** `python run_backtest.py ict [count]`.
- **Nghiên cứu:** `python research/ict.py` (train/OOS + walk-forward + theo tháng).

---

## 9. Lưu ý & rủi ro

- Mẫu backtest **còn nhỏ (34 lệnh)** → chưa đủ để khẳng định thống kê; nên **forward-test
  demo** trước khi tăng vốn.
- Win rate cao nhưng **RR < 1** → PP nhạy với spread/slippage. Chạy trên broker spread
  thấp và xác nhận lại với spread thực.
- Bias `prevday` là proxy đơn giản cho "daily bias" của ICT; trader ICT chuyên nghiệp
  còn đọc cấu trúc D1/H4, premium/discount, và điểm mất cân bằng chưa lấp — có thể tinh
  chỉnh thêm trong `strategies/ict.py`.
- Chỉ dùng trong phiên có thanh khoản tốt (XAUUSD, crypto/forex major). Không áp dụng
  máy móc cho thị trường thanh khoản kém.

> Đây là tài liệu kỹ thuật mô tả một phương pháp giao dịch, **không phải lời khuyên
> đầu tư**. Tự chịu trách nhiệm với mọi quyết định giao dịch.
