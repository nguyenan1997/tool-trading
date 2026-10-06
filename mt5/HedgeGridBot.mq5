//+------------------------------------------------------------------+
//|                                                HedgeGridBot.mq5  |
//|  Mô phỏng PP Hedging Grid của hệ thống (core/hedging_engine)      |
//|                                                                    |
//|  Luật:                                                             |
//|   - Mở đồng thời 1 BUY + 1 SELL, mỗi lệnh TP = InpTPUSD so giá vào|
//|   - Lệnh nào chạm TP -> đóng; mở 1 cặp BUY+SELL mới tại giá hiện tại|
//|   - KHÔNG SL (rủi ro vô hạn khi giá đi một chiều)                  |
//|   - Đạt lãi phiên (equity - đầu phiên >= InpTargetUSD) -> đóng hết |
//|   - Từ InpBalanceMinOrders lệnh: nếu BUY ≈ SELL -> đóng cả phiên   |
//|   - Đóng phiên (mục tiêu/cân bằng/hết margin) -> mở lại ĐÚNG 1 cặp |
//|   - Lưu mốc phiên ra file, tiếp quản vị thế khi khởi động lại     |
//+------------------------------------------------------------------+
#property copyright "tool-trading"
#property version   "1.22"
#property description "Hedging Grid: cap BUY+SELL, TP tung lenh, mo lai khi TP, muc tieu phien, can bang BUY/SELL, khong SL."

#include <Trade\Trade.mqh>

//────────────────────────────── Inputs ──────────────────────────────
input long   InpMagic           = 20260601;      // Magic number
input string InpComment         = "HedgeGrid_Bot";// Comment lệnh
input double InpLot             = 0.01;           // Lot mỗi lệnh
input double InpTPUSD           = 5.0;            // TP cách giá vào (USD; XAUUSD 1.0 = 1$)
input double InpTargetUSD       = 1100.0;         // Mục tiêu lãi phiên (USD; 0 = tắt)
input bool   InpStopAfterTarget = false;          // Dừng hẳn sau khi đạt mục tiêu
input bool   InpResetOnNoMargin = true;           // Hết margin -> đóng hết, chu kỳ mới
input int    InpMaxDevPts       = 30;             // Trượt tối đa (points)
input int    InpOpenRetries     = 3;              // Số lần thử mở mỗi chân
input int    InpPollMs          = 500;            // Chu kỳ xử lý (ms)
input string InpStateFile       = "hedge_session.txt"; // File lưu phiên (MQL5/Files)
input int    InpBalanceMinOrders = 400;           // Từ số lệnh này mới xét cân bằng BUY/SELL (0 = tắt)
input double InpBalancePct       = 0.05;          // |BUY-SELL| <= PCT*max(BUY,SELL) -> cân bằng -> đóng phiên
input bool   InpTradingHoursEnabled = false;      // Giới hạn giờ giao dịch (giờ VN) — khớp HEDGE_TRADING_HOURS_ENABLED
input string InpSkipHoursVN      = "";            // Khung giờ VN bị chặn, vd "0-6,23-24" — khớp HEDGE_SKIP_HOURS_VN
input int    InpVNUtcOffset      = 7;             // Múi giờ VN (UTC+7) — khớp VN_UTC_OFFSET

//────────────────────────────── Trạng thái ──────────────────────────────
CTrade   trade;
double   g_sessionEquity  = 0.0;
double   g_sessionBalance = 0.0;
datetime g_sessionTime    = 0;
long     g_sessionOrders  = 0;
bool     g_fresh          = true;
bool     g_stopRequested  = false;
bool     g_noMoney        = false;
ulong    g_known[];
bool     g_mktInit        = false;
double   g_lastBid        = 0.0;
uint     g_lastChangeMs   = 0;

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

// Giờ Việt Nam hiện tại — khớp Python _vn_now() = UTC + VN_UTC_OFFSET
int VNHour()
{
   datetime vn = TimeGMT() + InpVNUtcOffset * 3600;
   MqlDateTime d;
   TimeToStruct(vn, d);
   return d.hour;
}

// True nếu giờ VN hiện tại nằm trong khung bị chặn — khớp HEDGE_SKIP_HOURS_VN
bool InSkipHours()
{
   if(!InpTradingHoursEnabled) return false;
   if(StringLen(InpSkipHoursVN) == 0) return false;
   int h = VNHour();
   string parts[];
   string sepP = ",";
   int k = StringSplit(InpSkipHoursVN, StringGetCharacter(sepP, 0), parts);
   for(int i = 0; i < k; i++)
   {
      string a = parts[i];
      StringTrimLeft(a);
      StringTrimRight(a);
      if(StringLen(a) == 0) continue;
      string se[];
      string sepD = "-";
      if(StringSplit(a, StringGetCharacter(sepD, 0), se) != 2) continue;
      int s = (int)StringToInteger(se[0]);
      int e = (int)StringToInteger(se[1]);
      if(h >= s && h < e) return true;
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

// đếm BUY/SELL trong các vị thế của magic
void CountSides(int &nb, int &ns)
{
   nb = 0; ns = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetInteger(POSITION_TYPE) == (long)POSITION_TYPE_BUY) nb++; else ns++;
   }
}

int GetOurTickets(ulong &arr[])
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
      arr[c] = tk;
      c++;
   }
   return c;
}

bool ContainsTicket(const ulong &arr[], ulong tk)
{
   for(int i = 0; i < ArraySize(arr); i++)
      if(arr[i] == tk) return true;
   return false;
}

// MQL5 không cho gán mảng trực tiếp -> copy thủ công
void CopyTickets(ulong &dst[], const ulong &src[])
{
   int n = ArraySize(src);
   ArrayResize(dst, n);
   for(int i = 0; i < n; i++) dst[i] = src[i];
}

// Đặt TP của mọi vị thế = giá mở ± InpTPUSD
void NormalizeTP()
{
   int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      long   typ  = PositionGetInteger(POSITION_TYPE);
      double want = (typ == (long)POSITION_TYPE_BUY) ? NormalizeDouble(open + InpTPUSD, d)
                                                     : NormalizeDouble(open - InpTPUSD, d);
      double cur  = PositionGetDouble(POSITION_TP);
      if(MathAbs(cur - want) > pt * 0.5)
         trade.PositionModify(tk, PositionGetDouble(POSITION_SL), want);
   }
}

// Neo TP cho MỘT vị thế theo giá khớp thực tế — khớp Python _open_pair phase 2
void NormalizeTpTicket(ulong tk)
{
   if(!PositionSelectByTicket(tk)) return;
   int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   long   typ  = PositionGetInteger(POSITION_TYPE);
   double want = (typ == (long)POSITION_TYPE_BUY) ? NormalizeDouble(open + InpTPUSD, d)
                                                  : NormalizeDouble(open - InpTPUSD, d);
   double cur  = PositionGetDouble(POSITION_TP);
   double pt   = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(MathAbs(cur - want) > pt * 0.5)
      trade.PositionModify(tk, PositionGetDouble(POSITION_SL), want);
}

// Mở 1 cặp BUY + SELL
bool OpenPair()
{
   double lot = NormLot(InpLot);
   int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   bool okBuy = false, okSell = false;
   int tries = (int)MathMax(1, InpOpenRetries);
   ulong opened[];
   int nOpen = 0;

   for(int attempt = 0; attempt < tries; attempt++)
   {
      MqlTick t;
      if(!SymbolInfoTick(_Symbol, t)) { Sleep(200); continue; }
      if(!okBuy)
      {
         if(trade.Buy(lot, _Symbol, 0.0, 0.0, NormalizeDouble(t.ask + InpTPUSD, d), InpComment))
         {
            okBuy = true;
            ArrayResize(opened, nOpen + 1);
            opened[nOpen++] = (ulong)trade.ResultOrder();
         }
      }
      if(!okSell)
      {
         if(SymbolInfoTick(_Symbol, t) &&
            trade.Sell(lot, _Symbol, 0.0, 0.0, NormalizeDouble(t.bid - InpTPUSD, d), InpComment))
         {
            okSell = true;
            ArrayResize(opened, nOpen + 1);
            opened[nOpen++] = (ulong)trade.ResultOrder();
         }
      }
      if(okBuy && okSell) break;
      Sleep(300);
   }

   // Neo TP theo giá khớp thực tế — CHỈ các chân vừa mở (khớp Python _open_pair phase 2)
   for(int i = 0; i < nOpen; i++) NormalizeTpTicket(opened[i]);

   int ok = (okBuy ? 1 : 0) + (okSell ? 1 : 0);
   if(ok < 2)   // khớp Python: chỉ mở được < 2 chân -> kiểm tra margin
   {
      double need = 0.0, freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lot, SymbolInfoDouble(_Symbol, SYMBOL_ASK), need))
         g_noMoney = (freeMargin < need + 0.01);
   }

   g_sessionOrders += ok;
   PrintFormat("[HEDGE] Mo cap: BUY=%s SELL=%s lot=%.2f", okBuy ? "OK" : "FAIL", okSell ? "OK" : "FAIL", lot);
   return (ok > 0);
}

// Đóng toàn bộ vị thế của magic
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
   // Khớp Python _close_all: xóa danh sách theo dõi + về trạng thái "phiên mới"
   // (nếu không, vòng Process kế tiếp thấy g_known cũ -> mở bù hàng loạt cặp)
   ArrayResize(g_known, 0);
   g_fresh = true;
   PrintFormat("[HEDGE] Da dong toan bo vi the");
}

//────────────────────────── Phiên (lưu/đọc/đặt lại) ──────────────────────────
void SaveState()
{
   int h = FileOpen(InpStateFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("[HEDGE] Khong ghi duoc state: ", InpStateFile); return; }
   FileWrite(h, g_sessionEquity);
   FileWrite(h, g_sessionBalance);
   FileWrite(h, (long)g_sessionTime);
   FileWrite(h, InpTargetUSD);
   FileClose(h);
}

void LoadState()
{
   if(!FileIsExist(InpStateFile)) return;
   int h = FileOpen(InpStateFile, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   double eq = FileReadNumber(h);
   double bal = FileReadNumber(h);
   long   tm = (long)FileReadNumber(h);
   FileClose(h);
   if(eq > 0)
   {
      g_sessionEquity  = eq;
      g_sessionBalance = bal;
      g_sessionTime    = (datetime)tm;
      PrintFormat("[HEDGE] Tiep tuc phien cu: equity moc %.2f | bat dau %s",
                  g_sessionEquity, TimeToString(g_sessionTime));
   }
}

void ResetSession()
{
   g_sessionEquity  = AccountInfoDouble(ACCOUNT_EQUITY);
   g_sessionBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_sessionTime    = TimeCurrent();
   g_sessionOrders  = 0;
   SaveState();
   PrintFormat("[HEDGE] PHIEN MOI: equity moc %.2f | muc tieu +%.0f$", g_sessionEquity, InpTargetUSD);
}

//────────────────────────────── Xử lý chính ──────────────────────────────
void Process()
{
   if(g_stopRequested) return;
   if(!IsMarketOpen()) return;

   g_noMoney = false;
   int n = CountOurPositions();
   ulong current[];
   GetOurTickets(current);

   // 1) Mốc & mục tiêu lãi phiên
   if(InpTargetUSD > 0)
   {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      if(g_sessionEquity <= 0)
      {
         g_sessionEquity  = eq;
         g_sessionBalance = AccountInfoDouble(ACCOUNT_BALANCE);
         g_sessionTime    = TimeCurrent();
         SaveState();
         PrintFormat("[HEDGE] Moc lai phien: equity dau %.2f | muc tieu +%.0f$", g_sessionEquity, InpTargetUSD);
      }
      else if(eq - g_sessionEquity >= InpTargetUSD)
      {
         PrintFormat("[HEDGE] DAT MUC TIEU +%.0f$ (equity %.2f vs moc %.2f) -> dong het",
                     InpTargetUSD, eq, g_sessionEquity);
         CloseAll();
         ResetSession();
         if(InpStopAfterTarget) { g_stopRequested = true; Print("[HEDGE] Dung bot theo cau hinh."); }
         return;
      }
   }

   // 2) Đóng phiên khi BUY ≈ SELL (từ InpBalanceMinOrders lệnh)
   if(InpBalanceMinOrders > 0 && g_sessionOrders >= InpBalanceMinOrders && n > 0)
   {
      int nb, ns;
      CountSides(nb, ns);
      int mx = (int)MathMax(nb, ns);
      if(mx > 0 && MathAbs(nb - ns) <= InpBalancePct * mx)
      {
         PrintFormat("[HEDGE] BUY=%d ~ SELL=%d (sau %d lenh) -> dong ca phien, phien moi",
                     nb, ns, (int)g_sessionOrders);
         CloseAll();
         ResetSession();
         return;
      }
   }

   // 2b) Giới hạn giờ giao dịch (giờ VN): chỉ chặn MỞ lệnh mới — khớp Python
   if(InSkipHours())
   {
      if(n > 0)
      {
         CopyTickets(g_known, current);
         g_fresh = false;
      }
      else
      {
         ArrayResize(g_known, 0);
         g_fresh = true;
      }
      return;
   }

   // 3) Lần đầu của magic: mở cặp đầu HOẶC tiếp quản
   if(g_fresh)
   {
      if(n > 0)
      {
         CopyTickets(g_known, current);
         g_fresh = false;
         PrintFormat("[HEDGE] Tiep quan %d vi the dang mo", n);
         NormalizeTP();
      }
      else
      {
         if(OpenPair())
         {
            GetOurTickets(g_known);
            g_fresh = false;
            Print("[HEDGE] Da mo cap BUY+SELL dau tien");
         }
      }
      return;
   }

   // 4) Lệnh biến mất = đã chạm TP -> mở bù 1 cặp cho mỗi lệnh đã đóng
   int closed = 0;
   for(int i = 0; i < ArraySize(g_known); i++)
      if(!ContainsTicket(current, g_known[i])) closed++;
   if(closed > 0)
   {
      PrintFormat("[HEDGE] %d lenh cham TP -> mo %d cap moi", closed, closed);
      for(int j = 0; j < closed; j++) OpenPair();
      GetOurTickets(current);
   }
   CopyTickets(g_known, current);

   // 5) Hết margin -> đóng hết, chu kỳ mới
   if(g_noMoney && InpResetOnNoMargin)
   {
      Print("[HEDGE] Het margin -> dong het, bat dau chu ky moi");
      CloseAll();
      ResetSession();
      g_fresh = true;
   }
}

//────────────────────────────── Event handlers ──────────────────────────────
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpMaxDevPts);
   trade.SetTypeFilling(FillingMode());
   trade.LogLevel(LOG_LEVEL_ERRORS);

   if(!SymbolInfoInteger(_Symbol, SYMBOL_SELECT) && !SymbolSelect(_Symbol, true))
   {
      Print("[HEDGE] Khong chon duoc symbol ", _Symbol);
      return INIT_FAILED;
   }

   LoadState();
   EventSetMillisecondTimer(InpPollMs);
   PrintFormat("[HEDGE] Khoi dong tren %s | lot=%.2f | TP=%.2f$ | muc tieu=%.0f$ | can bang tu %d lenh | magic=%d",
               _Symbol, InpLot, InpTPUSD, InpTargetUSD, InpBalanceMinOrders, (int)InpMagic);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   SaveState();
   Print("[HEDGE] Dung EA (reason=", reason, ") - da luu phien");
}

void OnTimer()
{
   Process();
}

void OnTick()
{
   // (Trống) — xử lý trong OnTimer, khớp Python poll mỗi HEDGE_POLL_SEC
}
//+------------------------------------------------------------------+
