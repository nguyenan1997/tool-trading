//+------------------------------------------------------------------+
//|                                                 BigMouseBot.mq5  |
//|  Mô phỏng PP BigMouse Hedging (core/bigmouse_engine.py) để        |
//|  BACK-TEST trên MT5 Strategy Tester.                              |
//|                                                                    |
//|  Luật:                                                             |
//|   1. Mở 1 lệnh MARKET "mỏ neo" (mặc định BUY), TP = InpTPUSD.     |
//|   2. Đặt LỆNH CHỜ STOP ngược chiều, lot = lot mỏ neo × MULT,      |
//|      cách giá vào InpHedgeTriggerUSD, có TP riêng InpHedgeTPUSD.  |
//|   3. Mỏ neo chạm TP -> hủy STOP -> kết thúc chu kỳ THẮNG.         |
//|   4. STOP khớp -> hedge (basket). Tổng lãi nổi ≥ InpBasketTPUSD   |
//|      -> đóng cả basket. Một chân đóng, chân kia còn -> chốt net.  |
//|   5. MARTINGALE: chu kỳ LỖ -> nhân lot; LÃI -> về lot gốc.        |
//|  Không SL cho mỏ neo (hedge thay SL) -> rủi ro cao.               |
//+------------------------------------------------------------------+
#property copyright "tool-trading"
#property version   "1.00"
#property description "BigMouse: mo neo + hedge STOP lot lon + basket TP + martingale. Khong SL."

#include <Trade\Trade.mqh>

//────────────────────────────── Inputs ──────────────────────────────
input long   InpMagic            = 20260602;        // Magic number
input string InpComment          = "BigMouse_Bot";  // Comment lệnh
input string InpDirection        = "BUY";           // Hướng mỏ neo: BUY | SELL
input double InpLot              = 0.01;            // Lot mỏ neo
input double InpTPUSD            = 5.0;             // TP mỏ neo cách giá vào (USD)
input double InpHedgeTriggerUSD  = 5.0;             // Khoảng cách đặt STOP hedge (USD)
input double InpHedgeTPUSD       = 10.0;            // TP của vị thế hedge (USD)
input double InpHedgeLotMult     = 2.0;             // Lot hedge = lot mỏ neo × mult
input double InpBasketTPUSD      = 12.0;            // Chốt cả basket khi lãi nổi đạt (0 = tắt)
input bool   InpMartingale       = true;            // Bật martingale sau chu kỳ lỗ
input double InpMartingaleMult   = 2.0;             // Hệ số nhân lot
input int    InpMartingaleMaxSteps = 5;             // Số bước martingale tối đa
input bool   InpOneCycleOnly     = false;           // Chỉ chạy 1 chu kỳ rồi dừng
input bool   InpStopAfterBasket  = false;           // Dừng bot sau khi chốt basket có lãi
input int    InpMaxDevPts        = 30;              // Trượt tối đa (points)
input int    InpOpenRetries      = 3;               // Số lần thử mở mỏ neo
input int    InpPollMs           = 1000;            // Chu kỳ xử lý (ms)
input string InpStateFile        = "bigmouse_state.txt"; // File lưu bước martingale (MQL5/Files)

//────────────────────────────── Trạng thái ──────────────────────────────
CTrade   trade;
string   g_dir             = "BUY";
int      g_step            = 0;
bool     g_fresh           = true;
bool     g_stopRequested   = false;
ulong    g_anchorTicket    = 0;
double   g_cycleStartBal   = 0.0;
ulong    g_knownPos[];
ulong    g_knownOrd[];
bool     g_mktInit         = false;
double   g_lastBid         = 0.0;
uint     g_lastChangeMs    = 0;

//────────────────────────────── Helpers ──────────────────────────────
ENUM_ORDER_TYPE_FILLING FillingMode()
{
   long f = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((f & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((f & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

bool IsMarketOpen()
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return false;
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != (long)SYMBOL_TRADE_MODE_FULL) return false;
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t) || t.bid <= 0.0) return false;
   uint now = GetTickCount();
   if(!g_mktInit) { g_mktInit = true; g_lastBid = t.bid; g_lastChangeMs = now; return false; }
   if(t.bid != g_lastBid) { g_lastBid = t.bid; g_lastChangeMs = now; return true; }
   return (now - g_lastChangeMs) < 300000;   // tick đứng yên > 5 phút -> coi như đóng
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

int CountOurPositions()
{
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      c++;
   }
   return c;
}

int GetOurPositionTickets(ulong &arr[])
{
   ArrayResize(arr, 0);
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      ArrayResize(arr, c + 1);
      arr[c++] = tk;
   }
   return c;
}

int CountOurOrders()
{
   int c = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      c++;
   }
   return c;
}

int GetOurOrderTickets(ulong &arr[])
{
   ArrayResize(arr, 0);
   int c = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      ArrayResize(arr, c + 1);
      arr[c++] = tk;
   }
   return c;
}

bool ContainsTicket(const ulong &arr[], ulong tk)
{
   for(int i = 0; i < ArraySize(arr); i++)
      if(arr[i] == tk) return true;
   return false;
}

void CopyTickets(ulong &dst[], const ulong &src[])
{
   int n = ArraySize(src);
   ArrayResize(dst, n);
   for(int i = 0; i < n; i++) dst[i] = src[i];
}

double SumProfit()
{
   double s = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      s += PositionGetDouble(POSITION_PROFIT);
   }
   return s;
}

void CancelAllOrders()
{
   for(int guard = 0; guard < 60; guard++)
   {
      int n = CountOurOrders();
      if(n == 0) break;
      for(int i = OrdersTotal() - 1; i >= 0; i--)
      {
         ulong tk = OrderGetTicket(i);
         if(tk == 0) continue;
         if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
         if(OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
         trade.OrderDelete(tk);
      }
      Sleep(150);
   }
}

void CloseAll()
{
   for(int guard = 0; guard < 60; guard++)
   {
      if(CountOurPositions() == 0) break;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tk = PositionGetTicket(i);
         if(tk == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
         trade.PositionClose(tk, (ulong)InpMaxDevPts);
      }
      Sleep(200);
   }
   CancelAllOrders();
   Print("[BIGMOUSE] Da dong toan bo basket");
}

//────────────────────────── State (bước martingale) ──────────────────────────
void SaveState()
{
   int h = FileOpen(InpStateFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("[BIGMOUSE] Khong ghi duoc state: ", InpStateFile); return; }
   FileWrite(h, g_step);
   FileClose(h);
}

void LoadState()
{
   if(!FileIsExist(InpStateFile)) return;
   int h = FileOpen(InpStateFile, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   g_step = (int)FileReadNumber(h);
   FileClose(h);
   if(g_step != 0) PrintFormat("[BIGMOUSE] Tiep tuc buoc martingale = %d", g_step);
}

//────────────────────────── Lot theo martingale ──────────────────────────
double NextLot()
{
   if(!InpMartingale) return NormLot(InpLot);
   int st = (int)MathMin(g_step, InpMartingaleMaxSteps);
   double mult = (InpMartingaleMult > 0.0) ? InpMartingaleMult : 1.0;
   return NormLot(InpLot * MathPow(mult, st));
}

//────────────────────────── Mở / đặt lệnh ──────────────────────────
bool PlaceHedgeStop(double lot, double trigger, double hedgeTP)
{
   int    digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pt       = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    stops    = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist  = (double)(stops + 1) * pt;
   MqlTick t;
   if(!SymbolInfoTick(_Symbol, t)) return false;
   double price = NormalizeDouble(trigger, digits);
   if(g_dir != "SELL")   // mỏ neo BUY -> SELL STOP
   {
      double maxAllowed = NormalizeDouble(t.bid - minDist, digits);
      if(price > maxAllowed) price = maxAllowed;
      return trade.SellStop(lot, price, _Symbol, 0.0, hedgeTP, ORDER_TIME_GTC, 0, InpComment);
   }
   double minAllowed = NormalizeDouble(t.ask + minDist, digits);
   if(price < minAllowed) price = minAllowed;
   return trade.BuyStop(lot, price, _Symbol, 0.0, hedgeTP, ORDER_TIME_GTC, 0, InpComment);
}

bool OpenCycle()
{
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double lot    = NextLot();
   bool   isBuy  = (g_dir != "SELL");
   int    tries  = (int)MathMax(1, InpOpenRetries);
   bool   opened = false;

   // 1) Lệnh MARKET mỏ neo
   for(int attempt = 0; attempt < tries; attempt++)
   {
      MqlTick t;
      if(!SymbolInfoTick(_Symbol, t)) { Sleep(200); continue; }
      if(isBuy)
      {
         if(trade.Buy(lot, _Symbol, 0.0, 0.0, NormalizeDouble(t.ask + InpTPUSD, digits), InpComment)) { opened = true; break; }
      }
      else
      {
         if(trade.Sell(lot, _Symbol, 0.0, 0.0, NormalizeDouble(t.bid - InpTPUSD, digits), InpComment)) { opened = true; break; }
      }
      Sleep(300);
   }
   if(!opened)
   {
      Print("[BIGMOUSE] Khong mo duoc mo neo, thu lai vong sau");
      return false;
   }

   // 2) Tìm ticket mỏ neo vừa mở (theo hướng + chưa nằm trong danh sách theo dõi)
   ulong  anchor = 0;
   double entry  = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      long typ = PositionGetInteger(POSITION_TYPE);
      bool posBuy = (typ == (long)POSITION_TYPE_BUY);
      if(posBuy != isBuy) continue;
      if(ContainsTicket(g_knownPos, tk)) continue;
      anchor = tk;
      entry  = PositionGetDouble(POSITION_PRICE_OPEN);
      break;
   }
   if(anchor == 0)
   {
      Print("[BIGMOUSE] Khong tim thay ticket mo neo sau khi mo");
      return false;
   }

   // 3) Lệnh CHỜ STOP hedge ngược chiều
   double hedgeLot = NormLot(lot * ((InpHedgeLotMult > 0.0) ? InpHedgeLotMult : 1.0));
   double trigger, hedgeTP;
   if(isBuy)
   {
      trigger = entry - InpHedgeTriggerUSD;
      hedgeTP = trigger - InpHedgeTPUSD;
   }
   else
   {
      trigger = entry + InpHedgeTriggerUSD;
      hedgeTP = trigger + InpHedgeTPUSD;
   }
   hedgeTP = NormalizeDouble(hedgeTP, digits);
   bool stopOk = PlaceHedgeStop(hedgeLot, trigger, hedgeTP);
   ulong ordTicket = stopOk ? (ulong)trade.ResultOrder() : 0;

   g_cycleStartBal = AccountInfoDouble(ACCOUNT_BALANCE);
   g_anchorTicket  = anchor;
   ArrayResize(g_knownPos, 0); ArrayResize(g_knownPos, 1); g_knownPos[0] = anchor;
   if(ordTicket != 0) { ArrayResize(g_knownOrd, 1); g_knownOrd[0] = ordTicket; }
   else ArrayResize(g_knownOrd, 0);
   g_fresh = false;

   PrintFormat("[BIGMOUSE] CHU KY MOI | mo neo %s lot=%.2f entry=%.5f TP=%.5f | hedge lot=%.2f STOP @%.5f TP=%.5f | step=%d",
               isBuy ? "BUY" : "SELL", lot, entry, isBuy ? entry + InpTPUSD : entry - InpTPUSD,
               hedgeLot, trigger, hedgeTP, g_step);
   if(ordTicket == 0)
      Print("[BIGMOUSE] CANH BAO: khong dat duoc STOP hedge -> mo neo chay KHONG hedge, rui ro cao!");
   return true;
}

void Adopt(const ulong &pos[], const ulong &ord[])
{
   CopyTickets(g_knownPos, pos);
   CopyTickets(g_knownOrd, ord);
   g_anchorTicket = 0;
   for(int i = 0; i < ArraySize(pos); i++)
   {
      if(!PositionSelectByTicket(pos[i])) continue;
      long typ = PositionGetInteger(POSITION_TYPE);
      bool posBuy = (typ == (long)POSITION_TYPE_BUY);
      if((g_dir != "SELL" && posBuy) || (g_dir == "SELL" && !posBuy)) { g_anchorTicket = pos[i]; break; }
   }
   if(g_anchorTicket == 0 && ArraySize(pos) > 0) g_anchorTicket = pos[0];
   if(g_cycleStartBal <= 0.0) g_cycleStartBal = AccountInfoDouble(ACCOUNT_BALANCE);
   PrintFormat("[BIGMOUSE] Tiep quan %d vi the + %d lenh cho (step=%d)",
               ArraySize(pos), ArraySize(ord), g_step);
}

//────────────────────────── Kết thúc chu kỳ ──────────────────────────
void EndCycle(bool won)
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   bool hasPnl = (g_cycleStartBal > 0.0);
   double pnl = hasPnl ? (bal - g_cycleStartBal) : 0.0;

   if(InpMartingale)
   {
      bool lost = hasPnl ? (pnl < 0.0) : (!won);
      if(!lost)
      {
         if(g_step != 0) PrintFormat("[BIGMOUSE] THANG -> ve lot goc (step %d -> 0, PnL=%.2f)", g_step, pnl);
         g_step = 0;
      }
      else
      {
         if(g_step < InpMartingaleMaxSteps) g_step++;
         double mult = (InpMartingaleMult > 0.0) ? InpMartingaleMult : 1.0;
         PrintFormat("[BIGMOUSE] LO %.2f$ -> tang step len %d (lot tiep theo = %.2f)",
                     pnl, g_step, InpLot * MathPow(mult, g_step));
      }
   }
   SaveState();

   ArrayResize(g_knownPos, 0);
   ArrayResize(g_knownOrd, 0);
   g_anchorTicket = 0;
   g_cycleStartBal = 0.0;
   g_fresh = true;

   if(InpOneCycleOnly)
   {
      Print("[BIGMOUSE] Hoan tat 1 chu ky (OneCycleOnly) -> dung bot");
      g_stopRequested = true;
   }
   else if(InpStopAfterBasket && won)
   {
      Print("[BIGMOUSE] Da chot basket co lai (StopAfterBasket) -> dung bot");
      g_stopRequested = true;
   }
}

//────────────────────────── Xử lý chính ──────────────────────────
void Process()
{
   if(g_stopRequested) return;
   if(!IsMarketOpen()) return;

   int nPos = CountOurPositions();
   ulong curPos[];
   GetOurPositionTickets(curPos);
   ulong curOrd[];
   GetOurOrderTickets(curOrd);
   int nOrd = ArraySize(curOrd);

   // 0) Chu kỳ mới HOẶC tiếp quản
   if(g_fresh)
   {
      if(nPos > 0 || nOrd > 0) { Adopt(curPos, curOrd); g_fresh = false; return; }
      OpenCycle();
      return;
   }

   // 1) Đóng basket khi tổng lãi nổi đạt mục tiêu
   if(nPos > 0 && InpBasketTPUSD > 0.0)
   {
      double fl = SumProfit();
      if(fl >= InpBasketTPUSD)
      {
         PrintFormat("[BIGMOUSE] BASKET TP %.0f$ (lai noi %.2f$) -> dong het", InpBasketTPUSD, fl);
         CloseAll();
         EndCycle(true);
         return;
      }
   }

   // 2) Mỏ neo biến mất mà KHÔNG có hedge (chạm TP)
   if(g_anchorTicket > 0 && !ContainsTicket(curPos, g_anchorTicket))
   {
      bool hasHedge = false;
      for(int i = 0; i < ArraySize(curPos); i++)
         if(curPos[i] != g_anchorTicket) { hasHedge = true; break; }
      if(!hasHedge && nOrd > 0) { CancelAllOrders(); ArrayResize(curOrd, 0); nOrd = 0; }
      if(nPos == 0 && nOrd == 0)
      {
         Print("[BIGMOUSE] Mo neo cham TP -> ket thuc chu ky THANG");
         EndCycle(true);
         return;
      }
   }

   // 3) Mọi thứ đã đóng -> kết thúc chu kỳ
   if(nPos == 0 && nOrd == 0)
   {
      bool won = (g_anchorTicket == 0) || !ContainsTicket(g_knownPos, g_anchorTicket);
      Print("[BIGMOUSE] Chu ky dong sach -> ket thuc");
      EndCycle(won);
      return;
   }

   // 4) STOP khớp: lệnh chờ biến mất + xuất hiện vị thế mới
   int triggered = 0;
   for(int i = 0; i < ArraySize(g_knownOrd); i++)
      if(!ContainsTicket(curOrd, g_knownOrd[i])) triggered++;
   int newPos = 0;
   for(int i = 0; i < ArraySize(curPos); i++)
      if(!ContainsTicket(g_knownPos, curPos[i])) newPos++;
   if(triggered > 0 && newPos > 0)
      PrintFormat("[BIGMOUSE] HEDGE KICH HOAT (%d vi the moi) -> basket dang duoc hedge", newPos);

   // 5) Một chân basket đã đóng (vd hedge TP) còn chân kia -> chốt net
   int gone = 0;
   for(int i = 0; i < ArraySize(g_knownPos); i++)
      if(!ContainsTicket(curPos, g_knownPos[i])) gone++;
   if(gone > 0 && nPos > 0 && nOrd == 0)
   {
      Print("[BIGMOUSE] Mot chan basket da dong -> chot net toan basket");
      CloseAll();
      EndCycle(true);
      return;
   }

   CopyTickets(g_knownPos, curPos);
   CopyTickets(g_knownOrd, curOrd);
}

//────────────────────────── Event handlers ──────────────────────────
int OnInit()
{
   g_dir = InpDirection;
   StringToUpper(g_dir);
   if(g_dir != "SELL") g_dir = "BUY";

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpMaxDevPts);
   trade.SetTypeFilling(FillingMode());
   trade.LogLevel(LOG_LEVEL_ERRORS);

   if(!SymbolInfoInteger(_Symbol, SYMBOL_SELECT) && !SymbolSelect(_Symbol, true))
   {
      Print("[BIGMOUSE] Khong chon duoc symbol ", _Symbol);
      return INIT_FAILED;
   }

   LoadState();
   EventSetMillisecondTimer(InpPollMs);
   PrintFormat("[BIGMOUSE] Khoi dong tren %s | dir=%s | lot=%.2f | TP=%.2f$ | trigger=%.2f$ | hedgeTP=%.2f$ | hedgeMult=%.2f | basketTP=%.2f$ | martingale=%s x%.2f (max %d) | magic=%d",
               _Symbol, g_dir, InpLot, InpTPUSD, InpHedgeTriggerUSD, InpHedgeTPUSD, InpHedgeLotMult,
               InpBasketTPUSD, InpMartingale ? "ON" : "OFF", InpMartingaleMult, InpMartingaleMaxSteps, (int)InpMagic);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   SaveState();
   Print("[BIGMOUSE] Dung EA (reason=", reason, ") - da luu step");
}

void OnTimer()
{
   Process();
}

void OnTick()
{
   // Xử lý mỗi tick để back-test khớp sát; OnTimer bổ sung khi live ít tick.
   Process();
}
//+------------------------------------------------------------------+
