//+------------------------------------------------------------------+
//|                                              TrendMomentum.mq5   |
//|  EA chạy trực tiếp trong MT5 — phương pháp Trend Momentum        |
//|  (khớp với tool-trading: EMA200+ADX H1, ATR M15, Donchian+RSI M1,|
//|   phiên 12-21, TP 2R, chốt 50%@1R, BE@1R)                        |
//|                                                                  |
//|  Gắn vào chart XAUUSD khung M1, bật Algo Trading.                |
//+------------------------------------------------------------------+
#property copyright "tool-trading"
#property version   "1.00"
#property description "Trend Momentum EA (XAUUSD M1)"

#include <Trade\Trade.mqh>

//--------------------- INPUTS ---------------------
input group "=== Điểm vào (M1) ==="
input int    InpLookback   = 20;      // Donchian lookback (nến)
input int    InpRsiPeriod  = 7;       // RSI period
input double InpRsiBuy     = 55;      // RSI mua >=
input double InpRsiSell    = 45;      // RSI bán <=

input group "=== Lọc xu hướng (H1) ==="
input int    InpEmaLen     = 200;     // EMA H1
input int    InpAdxLen     = 14;      // ADX H1 period
input double InpAdxThresh  = 22;      // ADX H1 tối thiểu

input group "=== Thoát lệnh ==="
input int    InpAtrLen     = 14;      // ATR M15 period
input double InpSlAtr      = 1.5;     // SL = x ATR(M15)
input double InpTpR        = 2.0;     // TP = x khoảng SL
input double InpBeAtR      = 1.0;     // Dời SL hòa vốn tại (R)
input double InpPartialPct = 50;      // Chốt một phần (%)
input double InpPartialAtR = 1.0;     // Chốt một phần tại (R)

input group "=== Bộ lọc ==="
input bool   InpUseSession = true;    // Chỉ trade trong phiên
input int    InpSessStart  = 12;      // Giờ bắt đầu (giờ broker)
input int    InpSessEnd    = 21;      // Giờ kết thúc (giờ broker, bao gồm)
input double InpMinAtrPct  = 0.5;     // ATR(M15) >= phân vị (0 = tắt)
input int    InpAtrRankM15 = 96;      // Số nến M15 để xếp hạng ATR (~1440 M1)

input group "=== Giao dịch ==="
input double InpLot        = 0.02;    // Khối lượng (lot)
input long   InpMagic      = 20260320;// Magic number
input int    InpSlippage   = 50;      // Trượt giá tối đa (points)
input string InpComment    = "TrendMomentum_EA";

//--------------------- GLOBALS ---------------------
int    hEmaH1  = INVALID_HANDLE;
int    hAdxH1  = INVALID_HANDLE;
int    hAtrM15 = INVALID_HANDLE;
int    hRsiM1  = INVALID_HANDLE;
CTrade trade;
datetime g_lastBar = 0;
ulong    g_partial[];   // các ticket đã chốt một phần

//+------------------------------------------------------------------+
int OnInit()
{
   hEmaH1  = iMA(_Symbol, PERIOD_H1,  InpEmaLen, 0, MODE_EMA, PRICE_CLOSE);
   hAdxH1  = iADX(_Symbol, PERIOD_H1, InpAdxLen);
   hAtrM15 = iATR(_Symbol, PERIOD_M15, InpAtrLen);
   hRsiM1  = iRSI(_Symbol, PERIOD_M1, InpRsiPeriod, PRICE_CLOSE);

   if(hEmaH1==INVALID_HANDLE || hAdxH1==INVALID_HANDLE ||
      hAtrM15==INVALID_HANDLE || hRsiM1==INVALID_HANDLE)
   {
      Print("Không tạo được indicator handle");
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFillingBySymbol(_Symbol);
   ArrayResize(g_partial, 0);

   Print("TrendMomentum EA started | ", _Symbol, " ", EnumToString((ENUM_TIMEFRAMES)_Period));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(hEmaH1  != INVALID_HANDLE) IndicatorRelease(hEmaH1);
   if(hAdxH1  != INVALID_HANDLE) IndicatorRelease(hAdxH1);
   if(hAtrM15 != INVALID_HANDLE) IndicatorRelease(hAtrM15);
   if(hRsiM1  != INVALID_HANDLE) IndicatorRelease(hRsiM1);
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
double Buf(int handle, int buffer, int shift)
{
   double b[];
   if(CopyBuffer(handle, buffer, shift, 1, b) != 1)
      return EMPTY_VALUE;
   return b[0];
}

bool IsNewM1Bar()
{
   datetime t = iTime(_Symbol, PERIOD_M1, 0);
   if(t == g_lastBar) return false;
   g_lastBar = t;
   return true;
}

double HighestHigh(int count, int start)
{
   double a[];
   if(CopyHigh(_Symbol, PERIOD_M1, start, count, a) <= 0) return EMPTY_VALUE;
   int idx = ArrayMaximum(a);
   return (idx < 0) ? EMPTY_VALUE : a[idx];
}

double LowestLow(int count, int start)
{
   double a[];
   if(CopyLow(_Symbol, PERIOD_M1, start, count, a) <= 0) return EMPTY_VALUE;
   int idx = ArrayMinimum(a);
   return (idx < 0) ? EMPTY_VALUE : a[idx];
}

// Xếp hạng ATR(M15) so với các nến M15 gần nhất (thay cho rolling 1440 M1)
double AtrRank()
{
   if(InpAtrRankM15 < 2) return 1.0;
   double one[];
   if(CopyBuffer(hAtrM15, 0, 1, 1, one) != 1) return 0.0;
   double cur = one[0];

   double a[];
   int copied = CopyBuffer(hAtrM15, 0, 1, InpAtrRankM15, a);
   if(copied <= 0) return 0.0;

   int lt = 0, eq = 0;
   for(int i = 0; i < copied; i++)
   {
      if(a[i] < cur) lt++;
      else if(a[i] == cur) eq++;
   }
   return (lt + (eq + 1) / 2.0) / copied;   // ~ pandas rank(pct=True)
}

bool HasPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(!PositionSelectByTicket(t)) continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         return true;
   }
   return false;
}

bool PartialDone(ulong t)
{
   for(int i = 0; i < ArraySize(g_partial); i++)
      if(g_partial[i] == t) return true;
   return false;
}

void MarkPartial(ulong t)
{
   if(PartialDone(t)) return;
   int n = ArraySize(g_partial);
   ArrayResize(g_partial, n + 1);
   g_partial[n] = t;
}

double NormalizeVolume(double v)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0) step = 0.01;
   v = MathFloor(v / step + 1e-9) * step;
   v = MathMax(vmin, MathMin(v, vmax));
   return NormalizeDouble(v, 2);
}

//+------------------------------------------------------------------+
//| Quản lý vị thế: chốt một phần + dời SL hòa vốn (lúc nến M1 đóng) |
//+------------------------------------------------------------------+
void ManagePosition()
{
   double c1 = iClose(_Symbol, PERIOD_M1, 1);
   if(c1 <= 0) return;

   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long   type  = PositionGetInteger(POSITION_TYPE);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl    = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);
      double vol   = PositionGetDouble(POSITION_VOLUME);
      if(sl <= 0) continue;

      double R = MathAbs(entry - sl);
      if(R <= 0) continue;

      double hw = (type == POSITION_TYPE_BUY) ? (c1 - entry) / R : (entry - c1) / R;

      // 1) Chốt một phần (1 lần / ticket)
      if(InpPartialPct > 0 && InpPartialAtR > 0 &&
         !PartialDone(ticket) && hw >= InpPartialAtR)
      {
         double half = NormalizeVolume(vol * InpPartialPct / 100.0);
         if(half >= vmin - 1e-9 && (vol - half) >= vmin - 1e-9)
         {
            if(trade.PositionClosePartial(ticket, half))
               Print("PARTIAL #", ticket, " dong ", DoubleToString(half,2),
                     ", con ", DoubleToString(vol-half,2));
         }
         MarkPartial(ticket);   // đánh dấu dù thành công hay không (như bot Python)
      }

      // 2) Dời SL về hòa vốn
      if(InpBeAtR > 0 && hw >= InpBeAtR)
      {
         double newSl = (type == POSITION_TYPE_BUY)
                        ? MathMax(sl, entry)
                        : MathMin(sl, entry);
         newSl = NormalizeDouble(newSl, _Digits);
         if(MathAbs(newSl - sl) > _Point / 2.0)
         {
            if(trade.PositionModify(ticket, newSl, tp))
               Print("BE-MOVE #", ticket, " SL ", DoubleToString(sl,_Digits),
                     " -> ", DoubleToString(newSl,_Digits));
         }
      }
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewM1Bar()) return;

   // --- Dữ liệu nến ĐÃ ĐÓNG ---
   double emaH1 = Buf(hEmaH1, 0, 1);      // EMA200 H1 (nến H1 đã đóng)
   double adxH1 = Buf(hAdxH1, 0, 1);      // ADX H1
   double atr15 = Buf(hAtrM15, 0, 1);     // ATR M15 (nến M15 đã đóng)
   double rsi1  = Buf(hRsiM1, 0, 1);      // RSI M1
   double c1    = iClose(_Symbol, PERIOD_M1, 1);

   if(emaH1 == EMPTY_VALUE || adxH1 == EMPTY_VALUE ||
      atr15 == EMPTY_VALUE || rsi1 == EMPTY_VALUE) return;
   if(c1 <= 0 || atr15 <= 0) return;

   // --- Quản lý vị thế trước ---
   ManagePosition();
   if(HasPosition()) return;

   // --- Bộ lọc ---
   MqlDateTime dt;
   TimeToStruct(iTime(_Symbol, PERIOD_M1, 1), dt);
   bool inSess = (!InpUseSession) ||
                 (dt.hour >= InpSessStart && dt.hour <= InpSessEnd);
   if(!inSess) return;
   if(adxH1 < InpAdxThresh) return;
   if(InpMinAtrPct > 0 && AtrRank() < InpMinAtrPct) return;

   double priorHigh = HighestHigh(InpLookback, 1);
   double priorLow  = LowestLow(InpLookback, 1);
   if(priorHigh == EMPTY_VALUE || priorLow == EMPTY_VALUE) return;

   bool buy  = (c1 > emaH1) && (c1 > priorHigh) && (rsi1 >= InpRsiBuy);
   bool sell = (c1 < emaH1) && (c1 < priorLow)  && (rsi1 <= InpRsiSell);
   if(!buy && !sell) return;

   // --- Vào lệnh ---
   double dist = InpSlAtr * atr15;
   double ask  = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid  = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lot  = NormalizeVolume(InpLot);

   int    stopsLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist    = stopsLevel * _Point;

   if(buy)
   {
      if(dist < minDist) return;
      double sl = NormalizeDouble(ask - dist, _Digits);
      double tp = NormalizeDouble(ask + dist * InpTpR, _Digits);
      trade.Buy(lot, _Symbol, ask, sl, tp, InpComment);
   }
   else
   {
      if(dist < minDist) return;
      double sl = NormalizeDouble(bid + dist, _Digits);
      double tp = NormalizeDouble(bid - dist * InpTpR, _Digits);
      trade.Sell(lot, _Symbol, bid, sl, tp, InpComment);
   }
}
//+------------------------------------------------------------------+
