//+------------------------------------------------------------------+
//|                                                      ICTBot.mq5   |
//|  PP ICT: Bias -> Killzone -> Sweep -> CHoCH -> FVG (lọc size)     |
//|  -> LIMIT tại CE; SL sau quét; TP = draw on liquidity; partial 1R. |
//|  Port từ strategies/ict.py (XAUUSD M5).                           |
//|  Gắn vào chart XAUUSD khung M5, bật AutoTrading.                   |
//+------------------------------------------------------------------+
#property copyright "tool-trading"
#property version   "1.01"
#property description "ICT KZ-Sweep-CHoCH-FVG (XAUUSD M5)"

#include <Trade\Trade.mqh>

//────────────────────────────── Inputs ──────────────────────────────
input long   InpMagic       = 20260927;   // Magic
input string InpComment     = "ICT_Bot";  // Comment
input string InpBiasMode    = "h4ema";    // Bias: prevday | h4ema | none
input int    InpBiasEma     = 50;         // EMA H4 (khi bias = h4ema)
input double InpLot         = 0.02;       // Lot (khi InpRiskPercent=0)
input double InpRiskPercent  = 0.0;        // % rủi ro/lệnh theo SL (0 = dùng InpLot cố định)
input double InpMaxDDPercent = 0.0;        // Ngừng vào lệnh khi DD từ đỉnh >= X% (0=tắt)
input int    InpAsiaStart   = 0;          // Vùng Á - bắt đầu (giờ broker)
input int    InpAsiaEnd     = 6;          // Vùng Á - kết thúc
input int    InpKz1s        = 7;          // Killzone 1 bắt đầu (London open)
input int    InpKz1e        = 11;         // Killzone 1 kết thúc
input int    InpKz2s        = 12;         // Killzone 2 bắt đầu (NY open)
input int    InpKz2e        = 16;         // Killzone 2 kết thúc
input int    InpSwingK      = 2;          // Swing K (fractal)
input double InpMinSweepATR = 0.3;        // Độ sâu quét (×ATR)
input int    InpChochWait   = 36;         // Chờ CHoCH tối đa (nến M5) = 3h
input double InpDispATR     = 0.5;        // Displacement (×ATR)
input int    InpZoneLB      = 12;         // Tìm FVG trong (nến)
input int    InpHistoryBars = 5000;       // Nến M5 nạp để tính swing/thanh khoản (khớp ICT_HISTORY_BARS)
input double InpMinFvgATR   = 0.3;        // Lọc FVG size >= k×ATR (0=tắt)
input double InpEntryFrac   = 0.62;       // Vào tại (0.5=CE, 0.62=OTE)
input double InpSlBufATR    = 0.2;        // SL buffer (×ATR)
input double InpMaxRATR     = 6.0;        // R tối đa (×ATR)
input int    InpWarmupBars   = 600;       // Bỏ qua N nến M5 đầu (khớp warmup web ICT_WARMUP_BARS)
input double InpMinRATR      = 0.0;       // Bỏ setup nếu R < k×ATR (0=tắt) — khớp ICT_MIN_R_ATR
input int    InpVolMA        = 20;        // Chu kỳ MA volume — khớp ICT_VOL_MA
input double InpVolMin       = 0.0;       // Volume nến FVG >= k×volMA (0=tắt) — khớp ICT_VOL_MIN
input bool   InpSkipMitigated= false;     // Bỏ FVG đã bị lấp — khớp ICT_SKIP_MITIGATED
input double InpMitigateMax  = 0.5;       // Ngưỡng lấp (0..1) — khớp ICT_MITIGATE_MAX
input int    InpPendMin     = 120;        // Lệnh chờ hết hạn (phút)
input double InpPartFracPct = 0.5;        // Chốt % khi +1R (0 = tắt)
input double InpPartialAtR  = 1.0;        // Ngưỡng R chốt
input double InpBeAtR       = 0.0;        // Dời SL về hòa vốn khi đạt bội R (0 = tắt)
input int    InpMaxDevPts   = 30;         // Trượt tối đa (points)
input int    InpPollMs      = 500;        // Chu kỳ kiểm tra (ms)
input bool   InpManualSLTP  = true;       // Ẩn SL/TP khỏi sàn: tự cắt market khi chạm
input string InpManualFile  = "ict_manual_sltp.txt"; // File lưu SL/TP nội bộ (MQL5/Files)

CTrade trade;
datetime g_lastBar = 0;
ulong    g_partialDone = 0;
double   g_manSL = 0.0;      // SL/TP nội bộ (không gửi lên sàn)
double   g_manTP = 0.0;
bool     g_manActive = false;
double   g_manR = 0.0;       // R ban đầu của vị thế (cho BE)
bool     g_beDone = false;   // đã dời SL về hòa vốn chưa
string   g_biasMode = "prevday";
int      g_hH4ema = INVALID_HANDLE;
datetime g_startTime = 0;    // thời điểm bắt đầu (để bỏ qua warmup)
double   g_peakEquity = 0.0; // đỉnh equity (cho DD guard)

//────────────────────────────── Helpers ──────────────────────────────
int HourOf(datetime t) { MqlDateTime d; TimeToStruct(t, d); return d.hour; }
int DayId(datetime t)  { return (int)(t / 86400); }

int g_hATR = INVALID_HANDLE;
double ATR(int shift)
{
   if(g_hATR == INVALID_HANDLE) return 0.0;
   double b[];
   if(CopyBuffer(g_hATR, 0, shift, 1, b) != 1) return 0.0;
   return b[0];
}

double TickVol(int shift)
{
   long v[];
   if(CopyTickVolume(_Symbol, PERIOD_M5, shift, 1, v) != 1) return 0.0;
   return (double)v[0];
}

// EMA H4 của nến H4 ĐÃ ĐÓNG liền trước nến H4 chứa thời điểm t — khớp Python _add_bias(h4ema)
double H4EmaAt(datetime t)
{
   if(g_hH4ema == INVALID_HANDLE) return 0.0;
   int hs = iBarShift(_Symbol, PERIOD_H4, t, false);
   if(hs < 0) return 0.0;
   double b[];
   if(CopyBuffer(g_hH4ema, 0, hs + 1, 1, b) != 1) return 0.0;
   return b[0];
}

// Kiểm tra quét đáy: low thủng mức >= need rồi close reclaim
bool SweptLow(int i, const double &L[], const double &C[], double a,
              double asiaLo, double pdl, double lastSL, bool asiaOk)
{
   double need = InpMinSweepATR * a;
   double lv[3]; int m = 0;
   if(asiaOk) lv[m++] = asiaLo;
   lv[m++] = pdl;
   lv[m++] = lastSL;
   for(int j = 0; j < m; j++)
      if(lv[j] > 0 && L[i] < lv[j] - need && C[i] > lv[j]) return true;
   return false;
}
bool SweptHigh(int i, const double &H[], const double &C[], double a,
               double asiaHi, double pdh, double lastSH, bool asiaOk)
{
   double need = InpMinSweepATR * a;
   double lv[3]; int m = 0;
   if(asiaOk) lv[m++] = asiaHi;
   lv[m++] = pdh;
   lv[m++] = lastSH;
   for(int j = 0; j < m; j++)
      if(lv[j] > 0 && H[i] > lv[j] + need && C[i] < lv[j]) return true;
   return false;
}

// FVG tăng: low[m] > high[m-2] và low[m] < close[i]; lọc size/volume/mitigate
bool BullZone(int i, const double &H[], const double &L[], const double &C[], double atrI,
              const double &V[], const double &VM[], double &zl, double &zh)
{
   int lo = i - InpZoneLB; if(lo < 2) lo = 2;
   for(int m = i; m >= lo; m--)
   {
      if(L[m] > H[m - 2] && L[m] < C[i])
      {
         zl = H[m - 2]; zh = L[m];
         if(InpMinFvgATR > 0 && (zh - zl) < InpMinFvgATR * atrI) continue;
         if(InpVolMin > 0 && VM[m] > 0 && V[m] < InpVolMin * VM[m]) continue;
         if(InpSkipMitigated)
         {
            double size = zh - zl;
            if(size > 0 && m + 1 <= i)
            {
               double mn = L[m + 1];
               for(int x = m + 2; x <= i; x++) if(L[x] < mn) mn = L[x];
               if((zh - mn) / size >= InpMitigateMax) continue;
            }
         }
         return true;
      }
   }
   return false;
}
bool BearZone(int i, const double &H[], const double &L[], const double &C[], double atrI,
              const double &V[], const double &VM[], double &zl, double &zh)
{
   int lo = i - InpZoneLB; if(lo < 2) lo = 2;
   for(int m = i; m >= lo; m--)
   {
      if(H[m] < L[m - 2] && H[m] > C[i])
      {
         zl = H[m]; zh = L[m - 2];
         if(InpMinFvgATR > 0 && (zh - zl) < InpMinFvgATR * atrI) continue;
         if(InpVolMin > 0 && VM[m] > 0 && V[m] < InpVolMin * VM[m]) continue;
         if(InpSkipMitigated)
         {
            double size = zh - zl;
            if(size > 0 && m + 1 <= i)
            {
               double mx = H[m + 1];
               for(int x = m + 2; x <= i; x++) if(H[x] > mx) mx = H[x];
               if((mx - zl) / size >= InpMitigateMax) continue;
            }
         }
         return true;
      }
   }
   return false;
}

// TP = draw on liquidity: mức gần nhất phía đối diện >= minR*R, else entry + tpR*R
double DolTp(bool isBuy, double entry, double R, double pdh, double pdl,
             double asiaHi, double asiaLo, double lastSH, double lastSL)
{
   double best = 0, bestD = 1e18;
   double lv[3];
   if(isBuy) { lv[0] = pdh; lv[1] = asiaHi; lv[2] = lastSH; }
   else      { lv[0] = pdl; lv[1] = asiaLo; lv[2] = lastSL; }
   for(int j = 0; j < 3; j++)
   {
      double x = lv[j]; if(x <= 0) continue;
      double dist = isBuy ? (x - entry) : (entry - x);
      if(dist >= 0.0 && dist < bestD) { bestD = dist; best = x; }
   }
   if(best > 0) return best;
   return isBuy ? entry + 3.0 * R : entry - 3.0 * R;
}

//────────────────── Đánh giá tín hiệu tại nến ĐÓNG cuối cùng ──────────────────
bool EvalSignal(int &dir, double &entry, double &sl, double &tp)
{
   int nb = InpHistoryBars; if(nb < 60) nb = 60;
   double O[], H[], L[], C[], A[], BE[], V[], VM[];
   int HH[], DD[];
   ArrayResize(O, nb); ArrayResize(H, nb); ArrayResize(L, nb);
   ArrayResize(C, nb); ArrayResize(A, nb); ArrayResize(BE, nb);
   ArrayResize(V, nb); ArrayResize(VM, nb);
   ArrayResize(HH, nb); ArrayResize(DD, nb);

   int n = 0;
   for(int s = nb; s >= 1; s--)        // già -> mới (s=1 = nến đóng gần nhất)
   {
      datetime t = iTime(_Symbol, PERIOD_M5, s);
      if(t == 0) continue;
      O[n] = iOpen(_Symbol, PERIOD_M5, s);
      H[n] = iHigh(_Symbol, PERIOD_M5, s);
      L[n] = iLow(_Symbol, PERIOD_M5, s);
      C[n] = iClose(_Symbol, PERIOD_M5, s);
      A[n] = ATR(s);
      V[n] = TickVol(s);
      BE[n] = (g_biasMode == "h4ema") ? H4EmaAt(t) : 0.0;
      HH[n] = HourOf(t); DD[n] = DayId(t);
      n++;
   }
   if(n < 60) return false;

   // MA volume (rolling InpVolMA) — khớp Python vol_ma
   int vmw = (InpVolMA > 1) ? InpVolMA : 1;
   for(int j = 0; j < n; j++)
   {
      int a0 = j - vmw + 1; if(a0 < 0) a0 = 0;
      double sm = 0.0; int cnt = 0;
      for(int x = a0; x <= j; x++) { sm += V[x]; cnt++; }
      VM[j] = (cnt > 0) ? sm / cnt : 0.0;
   }

   // PDH/PDL + bias prevday (nến ngày hôm trước)
   double pdh = iHigh(_Symbol, PERIOD_D1, 1);
   double pdl = iLow(_Symbol, PERIOD_D1, 1);
   double d1o = iOpen(_Symbol, PERIOD_D1, 1);
   double d1c = iClose(_Symbol, PERIOD_D1, 1);
   int bias = (d1c > d1o) ? 1 : ((d1c < d1o) ? -1 : 0);

   // Vùng Á theo TỪNG NGÀY (chỉ dùng sau khi phiên Á kết thúc; 0 = chưa có)
   double asiaHi[], asiaLo[];
   ArrayResize(asiaHi, n); ArrayResize(asiaLo, n);
   for(int i = 0; i < n; )
   {
      int d = DD[i]; int j2 = i;
      double hi = 0, lo = 0;
      while(j2 < n && DD[j2] == d)
      {
         if(HH[j2] >= InpAsiaStart && HH[j2] <= InpAsiaEnd)
         {
            if(hi == 0 || H[j2] > hi) hi = H[j2];
            if(lo == 0 || L[j2] < lo) lo = L[j2];
         }
         j2++;
      }
      for(int x = i; x < j2; x++)
      {
         if(HH[x] > InpAsiaEnd) { asiaHi[x] = hi; asiaLo[x] = lo; }
         else { asiaHi[x] = 0; asiaLo[x] = 0; }
      }
      i = j2;
   }

   // Swing fractal + last swing (toàn bộ lịch sử nạp -> giống Python)
   int k = InpSwingK;
   double lastSH[], lastSL[];
   ArrayResize(lastSH, n); ArrayResize(lastSL, n);
   double curH = 0, curL = 0;
   for(int j = 0; j < n; j++)
   {
      int q = j - k;
      if(q >= k && q + k < n)
      {
         // Khớp Python: high[j] >= max(window) AND high[j] > max(left) AND high[j] >= max(right)
         double wmax = H[q];
         for(int x = q - k; x <= q + k; x++) if(H[x] > wmax) wmax = H[x];
         double lmax = H[q - 1];
         for(int x = q - k; x <= q - 1; x++) if(H[x] > lmax) lmax = H[x];
         double rmax = H[q + 1];
         for(int x = q + 1; x <= q + k; x++) if(H[x] > rmax) rmax = H[x];
         if(H[q] >= wmax && H[q] > lmax && H[q] >= rmax) curH = H[q];

         // low[j] <= min(window) AND low[j] < min(left) AND low[j] <= min(right)
         double wmin = L[q];
         for(int x = q - k; x <= q + k; x++) if(L[x] < wmin) wmin = L[x];
         double lmin = L[q - 1];
         for(int x = q - k; x <= q - 1; x++) if(L[x] < lmin) lmin = L[x];
         double rmin = L[q + 1];
         for(int x = q + 1; x <= q + k; x++) if(L[x] < rmin) rmin = L[x];
         if(L[q] <= wmin && L[q] < lmin && L[q] <= rmin) curL = L[q];
      }
      lastSH[j] = curH; lastSL[j] = curL;
   }

   // Killzone
   bool inKZ[];
   ArrayResize(inKZ, n);
   for(int j = 0; j < n; j++)
      inKZ[j] = (HH[j] >= InpKz1s && HH[j] < InpKz1e) || (HH[j] >= InpKz2s && HH[j] < InpKz2e);
   int maxKZend = (InpKz1e > InpKz2e) ? InpKz1e : InpKz2e;

   // State machine (reset theo ngày, giống Python)
   int curDate = -1;
   bool doneB = false, doneS = false, actB = false, actS = false;
   double sweepLow = 0, sweepHigh = 0, refHigh = 0, refLow = 0;
   int startB = -1, startS = -1;
   dir = 0;

   for(int i = 0; i < n; i++)
   {
      if(DD[i] != curDate)
      {
         curDate = DD[i];
         doneB = doneS = false;
         actB = actS = false;
         sweepLow = sweepHigh = 0; refHigh = refLow = 0;
         startB = startS = -1;
      }
      if(!inKZ[i])
      {
         if(HH[i] >= maxKZend) { actB = false; actS = false; }
         continue;
      }
      double a = A[i];
      if(!(a > 0)) continue;

      int barBias;
      if(g_biasMode == "h4ema")
         barBias = (BE[i] > 0) ? (C[i] > BE[i] ? 1 : (C[i] < BE[i] ? -1 : 0)) : 0;
      else
         barBias = bias;   // prevday
      bool allowB = (g_biasMode == "none") || (barBias > 0);
      bool allowS = (g_biasMode == "none") || (barBias < 0);

      // ---- BUY ----
      if(allowB && !doneB)
      {
         if(actB)
         {
            if(i - startB > InpChochWait) actB = false;
            else if(refHigh > 0 && C[i] > refHigh && (C[i] - O[i]) >= InpDispATR * a)
            {
               double zl, zh;
               if(BullZone(i, H, L, C, a, V, VM, zl, zh))
               {
                  double e = zh - InpEntryFrac * (zh - zl);
                  double s = MathMin(sweepLow, zl) - InpSlBufATR * a;
                  double R = e - s;
                  if(R > 0 && e < C[i] && R <= InpMaxRATR * a && R >= InpMinRATR * a)
                  {
                     double tpv = DolTp(true, e, R, pdh, pdl, asiaHi[i], asiaLo[i], lastSH[i], lastSL[i]);
                     if(i == n - 1) { dir = 1; entry = e; sl = s; tp = tpv; return true; }
                     doneB = true; actB = false;
                  }
               }
            }
         }
         if(!actB && !doneB)
         {
            bool asiaOk = HH[i] > InpAsiaEnd;
            if(SweptLow(i, L, C, a, asiaLo[i], pdl, lastSL[i], asiaOk))
            { actB = true; sweepLow = L[i]; refHigh = lastSH[i]; startB = i; }
         }
      }

      // ---- SELL ----
      if(allowS && !doneS)
      {
         if(actS)
         {
            if(i - startS > InpChochWait) actS = false;
            else if(refLow > 0 && C[i] < refLow && (O[i] - C[i]) >= InpDispATR * a)
            {
               double zl, zh;
               if(BearZone(i, H, L, C, a, V, VM, zl, zh))
               {
                  double e = zl + InpEntryFrac * (zh - zl);
                  double s = MathMax(sweepHigh, zh) + InpSlBufATR * a;
                  double R = s - e;
                  if(R > 0 && e > C[i] && R <= InpMaxRATR * a && R >= InpMinRATR * a)
                  {
                     double tpv = DolTp(false, e, R, pdh, pdl, asiaHi[i], asiaLo[i], lastSH[i], lastSL[i]);
                     if(i == n - 1) { dir = -1; entry = e; sl = s; tp = tpv; return true; }
                     doneS = true; actS = false;
                  }
               }
            }
         }
         if(!actS && !doneS)
         {
            bool asiaOk = HH[i] > InpAsiaEnd;
            if(SweptHigh(i, H, C, a, asiaHi[i], pdh, lastSH[i], asiaOk))
            { actS = true; sweepHigh = H[i]; refLow = lastSL[i]; startS = i; }
         }
      }
   }
   return false;
}

//────────────────────────────── Đặt lệnh ──────────────────────────────
bool HasOurPositionOrPending()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic) return true;
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if(OrderGetInteger(ORDER_MAGIC) == InpMagic) return true;
   }
   return false;
}

double NormLot(double lot)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0) step = 0.01;
   lot = MathFloor(lot / step + 0.5) * step;
   lot = MathMax(vmin, MathMin(vmax, lot));
   return NormalizeDouble(lot, 2);
}

// Lot theo % rủi ro (dựa khoảng SL). Nếu InpRiskPercent<=0 -> lot cố định InpLot.
double CalcLot(double entry, double sl)
{
   if(InpRiskPercent <= 0) return NormLot(InpLot);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk = bal * InpRiskPercent / 100.0;
   double slDist = MathAbs(entry - sl);
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv <= 0 || ts <= 0 || slDist <= 0) return NormLot(InpLot);
   double lossPerLot = (slDist / ts) * tv;
   if(lossPerLot <= 0) return NormLot(InpLot);
   return NormLot(risk / lossPerLot);
}

// DD guard: true nếu drawdown từ đỉnh vượt ngưỡng -> KHÔNG vào lệnh mới
bool DDGuardBlocked()
{
   if(InpMaxDDPercent <= 0) return false;
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq > g_peakEquity) g_peakEquity = eq;
   if(g_peakEquity <= 0) return false;
   return ((g_peakEquity - eq) / g_peakEquity * 100.0) >= InpMaxDDPercent;
}

void PlaceLimit(int dir, double entry, double sl, double tp)
{
   int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   MqlTick tick; if(!SymbolInfoTick(_Symbol, tick)) return;
   double spread = tick.ask - tick.bid;
   // BUY LIMIT đặt tại level+spread để khớp khi BID chạm level (giống bot Python/backtest)
   double raw = (dir > 0) ? (entry + spread) : entry;
   double e = NormalizeDouble(raw, d), s = NormalizeDouble(sl, d), t = NormalizeDouble(tp, d);
   double ask = NormalizeDouble(tick.ask, d), bid = NormalizeDouble(tick.bid, d);
   if(dir > 0 && e >= ask) { Print("[ICT] BUY LIMIT bỏ qua: price >= ask"); return; }
   if(dir < 0 && e <= bid) { Print("[ICT] SELL LIMIT bỏ qua: price <= bid"); return; }

   if(dir > 0 && t <= e) return;
   if(dir < 0 && t >= e) return;

   datetime expire = TimeCurrent() + InpPendMin * 60;
   double lot = CalcLot(e, s);   // lot cố định hoặc theo % rủi ro
   // Ẩn SL/TP khỏi sàn: nếu bật, KHÔNG gửi SL/TP lên broker; bot tự cắt market khi chạm
   double ordSL = InpManualSLTP ? 0.0 : s;
   double ordTP = InpManualSLTP ? 0.0 : t;
   bool ok;
   if(dir > 0)
      ok = trade.BuyLimit(lot, e, _Symbol, ordSL, ordTP, ORDER_TIME_SPECIFIED, expire, InpComment);
   else
      ok = trade.SellLimit(lot, e, _Symbol, ordSL, ordTP, ORDER_TIME_SPECIFIED, expire, InpComment);
   if(ok)
   {
      if(InpManualSLTP) { g_manSL = s; g_manTP = t; g_manActive = true; SaveManual(); }
      PrintFormat("[ICT] ĐẶT %s LIMIT | E=%.2f SL=%.2f TP=%.2f lot=%.2f%s", dir > 0 ? "BUY" : "SELL",
                  e, s, t, lot, InpManualSLTP ? "  [ẩn SL/TP trên sàn]" : "");
   }
   else
      PrintFormat("[ICT] Đặt lệnh thất bại: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
}

//────────────────────────── Quản lý SL/TP nội bộ ──────────────────────────
void SaveManual()
{
   int h = FileOpen(InpManualFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileWrite(h, g_manActive ? 1 : 0);
   FileWrite(h, g_manSL);
   FileWrite(h, g_manTP);
   FileClose(h);
}
void LoadManual()
{
   if(!FileIsExist(InpManualFile)) return;
   int h = FileOpen(InpManualFile, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   long act = (long)FileReadNumber(h);
   double sl = FileReadNumber(h);
   double tp = FileReadNumber(h);
   FileClose(h);
   if(act != 0 && sl > 0 && tp > 0)
   {
      g_manActive = true; g_manSL = sl; g_manTP = tp;
      PrintFormat("[ICT] Tiep quan SL/TP noi bo: SL=%.2f TP=%.2f", g_manSL, g_manTP);
   }
}

// Tìm vị thế của magic (1 lệnh). Trả true nếu có.
bool FindOurPosition(ulong &tk, long &typ, double &entry)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      tk = t; typ = PositionGetInteger(POSITION_TYPE); entry = PositionGetDouble(POSITION_PRICE_OPEN);
      return true;
   }
   return false;
}

// Kiểm tra và cắt market khi giá chạm SL/TP nội bộ (BUY theo BID, SELL theo ASK=bid+spread)
void ManageManualSLTP()
{
   if(!InpManualSLTP) return;
   ulong tk = 0; long typ = 0; double entry = 0.0;
   bool havePos = FindOurPosition(tk, typ, entry);
   if(havePos && g_manActive && g_manSL > 0 && g_manTP > 0)
   {
      MqlTick t; if(!SymbolInfoTick(_Symbol, t)) return;
      double price = (typ == (long)POSITION_TYPE_BUY) ? t.bid : t.ask;  // SELL: cộng spread
      bool hit = false; string why = "";
      if(typ == (long)POSITION_TYPE_BUY)
      {
         if(price <= g_manSL) { hit = true; why = "SL"; }
         else if(price >= g_manTP) { hit = true; why = "TP"; }
      }
      else
      {
         if(price >= g_manSL) { hit = true; why = "SL"; }
         else if(price <= g_manTP) { hit = true; why = "TP"; }
      }
      if(hit)
      {
         if(trade.PositionClose(tk, (ulong)InpMaxDevPts))
         {
            PrintFormat("[ICT] MANUAL %s | ticket=%I64u | price=%.2f", why, tk, price);
            g_manActive = false; SaveManual();
         }
      }
   }
   else if(!havePos)
   {
      // không còn vị thế: nếu cũng không còn lệnh chờ -> xóa SL/TP nội bộ
      bool hasPend = false;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         ulong o = OrderGetTicket(i);
         if(o == 0) continue;
         if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
         if(OrderGetInteger(ORDER_MAGIC) == InpMagic) { hasPend = true; break; }
      }
      if(!hasPend && g_manActive) { g_manActive = false; SaveManual(); }
   }
}

//─ Quản lý vị thế: chốt một phần tại 1R ─
void ManagePosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(tk == g_partialDone) continue;
      if(InpPartFracPct <= 0 || InpPartialAtR <= 0) continue;

      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = (InpManualSLTP && g_manActive && g_manSL > 0) ? g_manSL
                                                                 : PositionGetDouble(POSITION_SL);
      double R = MathAbs(entry - sl);
      if(R <= 0) continue;
      double price = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                     ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                     : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double fav = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? (price - entry) : (entry - price);
      if(fav >= InpPartialAtR * R)
      {
         double vol = PositionGetDouble(POSITION_VOLUME);
         double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
         double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
         double part = MathFloor((vol * InpPartFracPct) / step + 1e-9) * step;
         double rem = NormalizeDouble(vol - part, 2);
         if(part >= vmin - 1e-9 && rem >= vmin - 1e-9)
         {
            if(trade.PositionClosePartial(tk, part, (ulong)InpMaxDevPts))
            {
               g_partialDone = tk;
               PrintFormat("[ICT] Chốt một phần %s: %.2f lot", _Symbol, part);
            }
         }
         else g_partialDone = tk;   // không chia được -> đánh dấu để không thử lại
      }
   }
}

//─ Dời SL về hòa vốn (BE) khi giá đi được InpBeAtR×R ─
void ManageBreakEven()
{
   if(InpBeAtR <= 0) return;
   ulong tk = 0; long typ = 0; double entry = 0.0;
   if(!FindOurPosition(tk, typ, entry))
   {
      g_manR = 0.0; g_beDone = false;
      return;
   }
   double sl = (InpManualSLTP && g_manActive && g_manSL > 0) ? g_manSL
                                                             : PositionGetDouble(POSITION_SL);
   if(sl <= 0) return;
   if(g_manR <= 0) g_manR = MathAbs(entry - sl);
   if(g_manR <= 0) return;

   MqlTick t; if(!SymbolInfoTick(_Symbol, t)) return;
   double price = (typ == (long)POSITION_TYPE_BUY) ? t.bid : t.ask;
   double fav = (typ == (long)POSITION_TYPE_BUY) ? (price - entry) : (entry - price);
   if(g_beDone || fav < InpBeAtR * g_manR) return;

   if(InpManualSLTP)
   {
      if(!g_manActive) return;
      if(typ == (long)POSITION_TYPE_BUY && entry > g_manSL) { g_manSL = entry; SaveManual(); }
      else if(typ == (long)POSITION_TYPE_SELL && entry < g_manSL) { g_manSL = entry; SaveManual(); }
   }
   else
   {
      double tp = PositionGetDouble(POSITION_TP);
      double newsl = NormalizeDouble(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
      trade.PositionModify(tk, newsl, tp);
   }
   g_beDone = true;
   PrintFormat("[ICT] BE@%.2fR | ticket=%I64u | entry=%.2f", InpBeAtR, tk, entry);
}

//────────────────────────────── Events ──────────────────────────────
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpMaxDevPts);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);
   g_hATR = iATR(_Symbol, PERIOD_M5, 14);
   if(g_hATR == INVALID_HANDLE)
   {
      Print("[ICT] Không tạo được ATR handle");
      return INIT_FAILED;
   }
   g_biasMode = InpBiasMode;
   StringToLower(g_biasMode);
   if(g_biasMode == "h4ema")
   {
      g_hH4ema = iMA(_Symbol, PERIOD_H4, InpBiasEma, 0, MODE_EMA, PRICE_CLOSE);
      if(g_hH4ema == INVALID_HANDLE)
      {
         Print("[ICT] Không tạo được EMA H4 handle");
         return INIT_FAILED;
      }
   }
   LoadManual();
   g_startTime = iTime(_Symbol, PERIOD_M5, 0);
   g_peakEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   EventSetMillisecondTimer(InpPollMs);
   PrintFormat("[ICT] Khởi động %s M5 | lot=%.2f | risk%%=%.2f | maxDD%%=%.2f | bias=%s | warmup=%d | partial=%.2f@%.1fR | be=%.1fR | manualSLTP=%s",
               _Symbol, InpLot, InpRiskPercent, InpMaxDDPercent, g_biasMode, InpWarmupBars, InpPartFracPct, InpPartialAtR, InpBeAtR, InpManualSLTP ? "ON" : "OFF");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   SaveManual();
   if(g_hATR != INVALID_HANDLE) IndicatorRelease(g_hATR);
   if(g_hH4ema != INVALID_HANDLE) IndicatorRelease(g_hH4ema);
}

void OnTimer()
{
   // Kiểm tra SL/TP nội bộ MỖI lần poll (kể cả trong nến)
   ManageManualSLTP();
   ManageBreakEven();

   // chỉ xử lý tín hiệu khi có nến M5 mới đóng
   datetime t1 = iTime(_Symbol, PERIOD_M5, 1);
   if(t1 == 0 || t1 == g_lastBar) { ManagePosition(); return; }
   g_lastBar = t1;

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return;
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != (long)SYMBOL_TRADE_MODE_FULL) return;

   // Warmup: bỏ qua N nến M5 đầu (khớp web config.ICT_WARMUP_BARS)
   if(InpWarmupBars > 0 && g_startTime > 0)
   {
      int elapsed = iBarShift(_Symbol, PERIOD_M5, g_startTime, false);
      if(elapsed < InpWarmupBars) { ManagePosition(); return; }
   }

   ManagePosition();
   if(HasOurPositionOrPending()) return;
   if(DDGuardBlocked()) return;   // dừng vào lệnh mới khi DD từ đỉnh vượt ngưỡng

   int dir; double e, s, t;
   if(EvalSignal(dir, e, s, t)) PlaceLimit(dir, e, s, t);
}

void OnTick() { }   // xử lý trong OnTimer
//+------------------------------------------------------------------+
