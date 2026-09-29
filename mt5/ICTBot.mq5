//+------------------------------------------------------------------+
//|                                                      ICTBot.mq5   |
//|  PP ICT: Bias -> Killzone -> Sweep -> CHoCH -> FVG (lọc size)     |
//|  -> LIMIT tại CE; SL sau quét; TP = draw on liquidity; partial 1R. |
//|  Port từ strategies/ict.py (XAUUSD M5).                           |
//|  Gắn vào chart XAUUSD khung M5, bật AutoTrading.                   |
//+------------------------------------------------------------------+
#property copyright "tool-trading"
#property version   "1.00"
#property description "ICT KZ-Sweep-CHoCH-FVG (XAUUSD M5)"

#include <Trade\Trade.mqh>

//────────────────────────────── Inputs ──────────────────────────────
input long   InpMagic       = 20260927;   // Magic
input string InpComment     = "ICT_Bot";  // Comment
input double InpLot         = 0.02;       // Lot
input int    InpAsiaStart   = 0;          // Vùng Á - bắt đầu (giờ broker)
input int    InpAsiaEnd     = 6;          // Vùng Á - kết thúc
input int    InpKz1s        = 7;          // Killzone 1 bắt đầu
input int    InpKz1e        = 11;         // Killzone 1 kết thúc
input int    InpKz2s        = 12;         // Killzone 2 bắt đầu
input int    InpKz2e        = 16;         // Killzone 2 kết thúc
input int    InpSwingK      = 2;          // Swing K (fractal)
input double InpMinSweepATR = 0.3;        // Độ sâu quét (×ATR)
input int    InpChochWait   = 24;         // Chờ CHoCH tối đa (nến M5)
input double InpDispATR     = 0.4;        // Displacement (×ATR)
input int    InpZoneLB      = 12;         // Tìm FVG trong (nến)
input double InpMinFvgATR   = 0.5;        // Lọc FVG size >= k×ATR (0=tắt)
input double InpEntryFrac   = 0.5;        // Vào tại (0.5=CE)
input double InpSlBufATR    = 0.2;        // SL buffer (×ATR)
input double InpMaxRATR     = 6.0;        // R tối đa (×ATR)
input int    InpPendMin     = 120;        // Lệnh chờ hết hạn (phút)
input double InpPartialFrac = 0.5;        // Chốt % khi +1R
input double InpPartialAtR  = 1.0;        // Ngưỡng R chốt
input int    InpMaxDevPts   = 30;         // Trượt tối đa (points)
input int    InpPollMs      = 1000;       // Chu kỳ kiểm tra (ms)

CTrade trade;
datetime g_lastBar = 0;
ulong    g_partialDone = 0;

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

// FVG tăng: low[m] > high[m-2] và low[m] < close[i]; lọc size >= k*ATR
bool BullZone(int i, int n, const double &H[], const double &L[], const double &C[], double atrI,
              double &zl, double &zh)
{
   int lo = i - InpZoneLB; if(lo < 2) lo = 2;
   for(int m = i; m >= lo; m--)
   {
      if(L[m] > H[m - 2] && L[m] < C[i])
      {
         zl = H[m - 2]; zh = L[m];
         if(InpMinFvgATR > 0 && (zh - zl) < InpMinFvgATR * atrI) continue;
         return true;
      }
   }
   return false;
}
bool BearZone(int i, int n, const double &H[], const double &L[], const double &C[], double atrI,
              double &zl, double &zh)
{
   int lo = i - InpZoneLB; if(lo < 2) lo = 2;
   for(int m = i; m >= lo; m--)
   {
      if(H[m] < L[m - 2] && H[m] > C[i])
      {
         zl = H[m]; zh = L[m - 2];
         if(InpMinFvgATR > 0 && (zh - zl) < InpMinFvgATR * atrI) continue;
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
   datetime t1 = iTime(_Symbol, PERIOD_M5, 1);
   if(t1 == 0) return false;
   int dId = DayId(t1);
   int maxs = 1500, cnt = 0;
   for(int s = 1; s < maxs; s++)
   {
      datetime t = iTime(_Symbol, PERIOD_M5, s);
      if(t == 0) break;
      if(DayId(t) != dId) break;
      cnt++;
   }
   if(cnt < 12) return false;

   double O[], H[], L[], C[], A[]; int HH[], SH[], SLi[];
   datetime TT[];
   ArrayResize(O, cnt); ArrayResize(H, cnt); ArrayResize(L, cnt);
   ArrayResize(C, cnt); ArrayResize(A, cnt); ArrayResize(HH, cnt); ArrayResize(TT, cnt);
   for(int j = 0; j < cnt; j++)
   {
      int s = cnt - j;                 // s: cnt..1 → thời gian tăng dần
      O[j] = iOpen(_Symbol, PERIOD_M5, s);
      H[j] = iHigh(_Symbol, PERIOD_M5, s);
      L[j] = iLow(_Symbol, PERIOD_M5, s);
      C[j] = iClose(_Symbol, PERIOD_M5, s);
      A[j] = ATR(s);
      TT[j] = iTime(_Symbol, PERIOD_M5, s);
      HH[j] = HourOf(TT[j]);
   }
   int n = cnt;

   // PDH/PDL + bias prevday (nến ngày hôm trước)
   double pdh = iHigh(_Symbol, PERIOD_D1, 1);
   double pdl = iLow(_Symbol, PERIOD_D1, 1);
   double d1o = iOpen(_Symbol, PERIOD_D1, 1);
   double d1c = iClose(_Symbol, PERIOD_D1, 1);
   int bias = (d1c > d1o) ? 1 : ((d1c < d1o) ? -1 : 0);

   // Vùng Á hôm nay
   double aHi = 0, aLo = 0;
   for(int j = 0; j < n; j++)
      if(HH[j] >= InpAsiaStart && HH[j] <= InpAsiaEnd)
      {
         if(aHi == 0 || H[j] > aHi) aHi = H[j];
         if(aLo == 0 || L[j] < aLo) aLo = L[j];
      }

   // Swing fractal + last swing
   int k = InpSwingK;
   ArrayResize(SH, n); ArrayResize(SLi, n);
   for(int j = 0; j < n; j++) { SH[j] = 0; SLi[j] = 0; }
   double lastSH[1500], lastSL[1500];
   double curH = 0, curL = 0;
   for(int j = 0; j < n; j++)
   {
      int q = j - k;
      if(q >= k)
      {
         bool ph = true, pl = true;
         for(int x = q - k; x <= q + k; x++)
         {
            if(x < 0 || x >= n) { ph = pl = false; break; }
            if(H[x] > H[q]) ph = false;
            if(L[x] < L[q]) pl = false;
         }
         if(ph) { curH = H[q]; SH[q] = 1; }
         if(pl) { curL = L[q]; SLi[q] = 1; }
      }
      lastSH[j] = curH; lastSL[j] = curL;
   }

   // Killzone
   bool inKZ[1500];
   for(int j = 0; j < n; j++)
      inKZ[j] = (HH[j] >= InpKz1s && HH[j] < InpKz1e) || (HH[j] >= InpKz2s && HH[j] < InpKz2e);
   int maxKZend = (InpKz1e > InpKz2e) ? InpKz1e : InpKz2e;

   // State machine (giống Python)
   bool doneB = false, doneS = false, actB = false, actS = false;
   double sweepLow = 0, sweepHigh = 0, refHigh = 0, refLow = 0;
   int startB = -1, startS = -1;
   dir = 0;

   for(int i = 0; i < n; i++)
   {
      if(!inKZ[i])
      {
         if(HH[i] >= maxKZend) { actB = false; actS = false; }
         continue;
      }
      double a = A[i];
      if(!(a > 0)) continue;

      bool allowB = (bias >= 0);
      bool allowS = (bias <= 0);

      // ---- BUY ----
      if(allowB && !doneB)
      {
         if(actB)
         {
            if(i - startB > InpChochWait) actB = false;
            else if(C[i] > refHigh && (C[i] - O[i]) >= InpDispATR * a)
            {
               double zl, zh;
               if(BullZone(i, n, H, L, C, a, zl, zh))
               {
                  double e = zh - InpEntryFrac * (zh - zl);
                  double s = MathMin(sweepLow, zl) - InpSlBufATR * a;
                  double R = e - s;
                  if(R > 0 && e < C[i] && R <= InpMaxRATR * a)
                  {
                     double tpv = DolTp(true, e, R, pdh, pdl, aHi, aLo, lastSH[i], lastSL[i]);
                     if(i == n - 1) { dir = 1; entry = e; sl = s; tp = tpv; return true; }
                     doneB = true; actB = false;
                  }
               }
            }
         }
         if(!actB && !doneB)
         {
            bool asiaOk = HH[i] > InpAsiaEnd;
            if(SweptLow(i, L, C, a, aLo, pdl, lastSL[i], asiaOk))
            { actB = true; sweepLow = L[i]; refHigh = lastSH[i]; startB = i; }
         }
      }

      // ---- SELL ----
      if(allowS && !doneS)
      {
         if(actS)
         {
            if(i - startS > InpChochWait) actS = false;
            else if(C[i] < refLow && (O[i] - C[i]) >= InpDispATR * a)
            {
               double zl, zh;
               if(BearZone(i, n, H, L, C, a, zl, zh))
               {
                  double e = zl + InpEntryFrac * (zh - zl);
                  double s = MathMax(sweepHigh, zh) + InpSlBufATR * a;
                  double R = s - e;
                  if(R > 0 && e > C[i] && R <= InpMaxRATR * a)
                  {
                     double tpv = DolTp(false, e, R, pdh, pdl, aHi, aLo, lastSH[i], lastSL[i]);
                     if(i == n - 1) { dir = -1; entry = e; sl = s; tp = tpv; return true; }
                     doneS = true; actS = false;
                  }
               }
            }
         }
         if(!actS && !doneS)
         {
            bool asiaOk = HH[i] > InpAsiaEnd;
            if(SweptHigh(i, H, C, a, aHi, pdh, lastSH[i], asiaOk))
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

void PlaceLimit(int dir, double entry, double sl, double tp)
{
   int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double e = NormalizeDouble(entry, d), s = NormalizeDouble(sl, d), t = NormalizeDouble(tp, d);
   MqlTick tick; if(!SymbolInfoTick(_Symbol, tick)) return;
   if(dir > 0 && e >= tick.ask) { Print("[ICT] BUY LIMIT bỏ qua: level >= ask"); return; }
   if(dir < 0 && e <= tick.bid) { Print("[ICT] SELL LIMIT bỏ qua: level <= bid"); return; }

   if(dir > 0 && t <= e) return;
   if(dir < 0 && t >= e) return;

   datetime expire = TimeCurrent() + InpPendMin * 60;
   bool ok;
   if(dir > 0)
      ok = trade.BuyLimit(InpLot, e, _Symbol, s, t, ORDER_TIME_SPECIFIED, expire, InpComment);
   else
      ok = trade.SellLimit(InpLot, e, _Symbol, s, t, ORDER_TIME_SPECIFIED, expire, InpComment);
   if(ok)
      PrintFormat("[ICT] ĐẶT %s LIMIT | E=%.2f SL=%.2f TP=%.2f", dir > 0 ? "BUY" : "SELL", e, s, t);
   else
      PrintFormat("[ICT] Đặt lệnh thất bại: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
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
      if(InpPartialFrac <= 0 || InpPartialAtR <= 0) continue;

      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
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
         double part = MathFloor((vol * InpPartialFrac) / step + 1e-9) * step;
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
   EventSetMillisecondTimer(InpPollMs);
   PrintFormat("[ICT] Khởi động %s M5 | lot=%.2f | minFvgATR=%.2f", _Symbol, InpLot, InpMinFvgATR);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(g_hATR != INVALID_HANDLE) IndicatorRelease(g_hATR);
}

void OnTimer()
{
   // chỉ xử lý khi có nến M5 mới đóng
   datetime t1 = iTime(_Symbol, PERIOD_M5, 1);
   if(t1 == 0 || t1 == g_lastBar) { ManagePosition(); return; }
   g_lastBar = t1;

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return;
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != (long)SYMBOL_TRADE_MODE_FULL) return;

   ManagePosition();
   if(HasOurPositionOrPending()) return;

   int dir; double e, s, t;
   if(EvalSignal(dir, e, s, t)) PlaceLimit(dir, e, s, t);
}

void OnTick() { }   // xử lý trong OnTimer
//+------------------------------------------------------------------+
