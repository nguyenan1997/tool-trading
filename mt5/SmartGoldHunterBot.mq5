//+------------------------------------------------------------------+
//|                                      SmartGoldHunterBot.mq5      |
//|  Phong theo EA "Smart Gold Hunter" (MQL5 product 170050) — XAUUSD |
//|                                                                  |
//|  Triet ly: No Grid / No Martingale / No Recovery / No Hedging     |
//|            => SINGLE ENTRY / ONE SHOT, moi setup 1 lenh,          |
//|               co SL/TP that, Break Even + trailing, nhieu profile |
//|               + lop bao ve.                                      |
//|                                                                  |
//|  Entry : pha vo kenh Donchian (dinh/day N nen) + than nen + EMA.  |
//|  Quan ly: SL theo ATR, TP = boi R, BE, trailing, chot mot phan.   |
//|  Bao ve : gioi han lai/lo ngay, spread, news, dong lenh thu 6.    |
//|                                                                  |
//|  Port tu strategies/sgh.py. Gan vao chart XAUUSD, bat AutoTrading.|
//+------------------------------------------------------------------+
#property copyright "tool-trading"
#property version   "1.00"
#property description "Smart Gold Hunter (XAUUSD): Donchian breakout + body + EMA, SL/TP that, BE/trail/partial, bao ve."

#include <Trade\Trade.mqh>

//────────────────────────────── Enums ──────────────────────────────
enum ENUM_SGH_PROFILE
{
   SGH_STRIKER      = 0,   // Striker (mac dinh EA goc)
   SGH_ULT_SCALPER  = 1,   // Ultimate Scalper
   SGH_SWINGER      = 2,   // Swinger
   SGH_PROP_SCALPER = 3,   // Prop Scalper
   SGH_PRR_SCALPER  = 4,   // PRR Scalping
   SGH_CUSTOM       = 5    // Custom (dung tham so ben duoi)
};

enum ENUM_SGH_TREND
{
   SGH_TREND_NONE = 0,     // none
   SGH_TREND_EMA  = 1      // ema
};

//────────────────────────────── Inputs ──────────────────────────────
input group "=== Chung ==="
input long             InpMagic      = 20260605;          // Magic
input string           InpComment    = "SmartGoldHunter"; // Comment
input ENUM_TIMEFRAMES  InpTimeframe  = PERIOD_M15;        // Khung thoi gian (Python: M15)
input ENUM_SGH_PROFILE InpProfile    = SGH_PRR_SCALPER;   // Profile
input double           InpLot        = 0.02;              // Lot
input int              InpHistoryBars= 3000;              // So nen nap de tinh tin hieu

input group "=== Entry (breakout Donchian + xac nhan) ==="
input int             InpBreakLookback = 20;              // Dinh/day N nen (Custom / bi profile de)
input double          InpBreakBufATR   = 0.1;             // Buffer pha vo (x ATR)
input double          InpMinBodyATR    = 0.30;            // Than nen toi thieu (x ATR)
input ENUM_SGH_TREND  InpTrendMode     = SGH_TREND_EMA;   // Loc trend
input int             InpEmaPeriod     = 50;              // EMA chu ky
input int             InpMaxTradesDay  = 3;               // So lenh toi da / ngay
input int             InpMinBarsBetween= 3;               // Toi thieu so nen giua 2 lenh

input group "=== SL/TP ==="
input double InpSlATR = 1.5;   // SL = entry -/ + boi ATR
input double InpTpR   = 1.5;   // TP = boi R

input group "=== Quan ly lenh ==="
input double InpBeAtR      = 1.0;   // Doi SL ve hoa von khi dat X R
input double InpTrailAtR   = 1.0;   // Bat dau trailing sau X R
input double InpTrailGapR  = 0.8;   // SL bam cach gia tot nhat Y R
input double InpPartialFrac= 0.0;   // Chot mot phan (0 = tat)
input double InpPartialAtR = 1.0;   // Nguong R chot mot phan

input group "=== Lop bao ve ==="
input int    InpMaxSpreadPoints      = 40;    // Khong vao lenh neu spread > muc nay (points, 0=tat)
input int    InpFridayCloseHour      = 21;    // >= gio nay thu 6 -> khong vao lenh (25 = tat)
input bool   InpNewsFilter           = true;  // Bat loc tin
input int    InpNewsHour             = 12;    // Gio tin manh (thu 6)
input int    InpNewsMinute           = 30;    // Phut tin manh
input int    InpNewsSkipMin          = 45;    // Bo qua +/- phut quanh tin
input string InpNewsTimes            = "";    // Them gio tin "HH:MM,HH:MM"
input double InpDailyProfitTargetPct = 0.0;   // Muc tieu lai ngay (%) -> nghi (0=tat)
input double InpDailyLossLimitPct    = 3.0;   // Lo ngay vuot muc (%) -> nghi (0=tat)
input double InpEquityProtectionPct  = 5.0;   // Equity giam qua X% tu dinh ngay -> nghi (0=tat)

input group "=== Kieu SL / randomizer (giong ban goc) ==="
input bool InpSlFromExec         = true;  // SL/TP tinh tu GIA KHOP thuc te
input bool InpHideInitialSL      = false; // An SL/TP ban dau khoi san (tu cat noi bo)
input int  InpEntryRandomPoints  = 0;     // Lech SL/TP ngau nhien +/- N points (0=tat)

input group "=== Thuc thi ==="
input string InpManualFile = "sgh_manual_sltp.txt"; // File luu SL/TP noi bo (MQL5/Files)
input int    InpMaxDevPts  = 30;   // Truot toi da (points)
input int    InpPollMs     = 500;  // Chu ky kiem tra (ms)

//────────────────────── Tham so hieu luc (sau profile) ──────────────────
double g_tpR        = 1.5;
double g_slATR      = 1.5;
double g_minBodyATR = 0.30;
int    g_lookback   = 20;
int    g_maxTrades  = 3;
double g_beAtR      = 1.0;
double g_trailAtR   = 1.0;
double g_trailGapR  = 0.8;
double g_partialFrac= 0.0;
double g_partialAtR = 1.0;

//────────────────────────────── Trang thai ──────────────────────────────
CTrade   trade;
int      g_hATR = INVALID_HANDLE;
int      g_hEMA = INVALID_HANDLE;
datetime g_lastBar = 0;
datetime g_lastSignalTraded = 0;

ulong    g_posTicket   = 0;
double   g_initR       = 0.0;
bool     g_beDone      = false;
ulong    g_partialDone = 0;
double   g_trailBest   = 0.0;

double   g_manSL = 0.0;
double   g_manTP = 0.0;
bool     g_manActive = false;

int      g_dayId = -1;
double   g_dayStartBalance = 0.0;
double   g_eqPeak = 0.0;

int      g_newsH[32];
int      g_newsM[32];
int      g_newsN = 0;

//────────────────────────────── Helpers ──────────────────────────────
int HourOf(datetime t) { MqlDateTime d; TimeToStruct(t, d); return d.hour; }
int DayId(datetime t)  { return (int)(t / 86400); }

void ParseNews()
{
   g_newsN = 0;
   string s = InpNewsTimes;
   StringTrimLeft(s); StringTrimRight(s);
   if(StringLen(s) == 0) return;
   string parts[];
   int k = StringSplit(s, ',', parts);
   for(int i = 0; i < k && g_newsN < 32; i++)
   {
      string t = parts[i];
      StringTrimLeft(t); StringTrimRight(t);
      int c = StringFind(t, ":");
      if(c <= 0) continue;
      g_newsH[g_newsN] = (int)StringToInteger(StringSubstr(t, 0, c));
      g_newsM[g_newsN] = (int)StringToInteger(StringSubstr(t, c + 1));
      g_newsN++;
   }
}

bool InNewsWindow(datetime t)
{
   if(!InpNewsFilter) return false;
   MqlDateTime d; TimeToStruct(t, d);
   int mod = d.hour * 60 + d.min;
   for(int i = 0; i < g_newsN; i++)
      if(MathAbs(mod - (g_newsH[i] * 60 + g_newsM[i])) <= InpNewsSkipMin) return true;
   // Nhu Python: chi them slot NFP vao THU 6
   if(d.day_of_week == 5 && MathAbs(mod - (InpNewsHour * 60 + InpNewsMinute)) <= InpNewsSkipMin)
      return true;
   return false;
}

void ApplyProfile()
{
   switch(InpProfile)
   {
      case SGH_STRIKER:
         g_tpR = 1.5; g_slATR = 1.5; g_minBodyATR = 0.30; g_lookback = 20; g_maxTrades = 3;
         g_beAtR = 1.0; g_trailAtR = 1.0; g_trailGapR = 0.8; g_partialFrac = 0.0;
         break;
      case SGH_ULT_SCALPER:
         g_tpR = 1.0; g_slATR = 1.0; g_minBodyATR = 0.20; g_lookback = 10; g_maxTrades = 6;
         g_beAtR = 0.7; g_trailAtR = 0.8; g_trailGapR = 0.6; g_partialFrac = 0.0;
         break;
      case SGH_SWINGER:
         g_tpR = 3.0; g_slATR = 2.0; g_minBodyATR = 0.40; g_lookback = 40; g_maxTrades = 1;
         g_beAtR = 1.5; g_trailAtR = 1.5; g_trailGapR = 1.0; g_partialFrac = 0.0;
         break;
      case SGH_PROP_SCALPER:
         g_tpR = 1.2; g_slATR = 1.0; g_minBodyATR = 0.30; g_lookback = 15; g_maxTrades = 2;
         g_beAtR = 0.8; g_trailAtR = 0.8; g_trailGapR = 0.6; g_partialFrac = 0.0;
         break;
      case SGH_PRR_SCALPER:
         g_tpR = 2.0; g_slATR = 1.0; g_minBodyATR = 0.25; g_lookback = 12; g_maxTrades = 4;
         g_beAtR = 1.0; g_trailAtR = 1.0; g_trailGapR = 0.7; g_partialFrac = 0.0;
         break;
      case SGH_CUSTOM:
      default:
         g_tpR = InpTpR; g_slATR = InpSlATR; g_minBodyATR = InpMinBodyATR; g_lookback = InpBreakLookback;
         g_maxTrades = InpMaxTradesDay; g_beAtR = InpBeAtR; g_trailAtR = InpTrailAtR;
         g_trailGapR = InpTrailGapR; g_partialFrac = InpPartialFrac; g_partialAtR = InpPartialAtR;
         break;
   }
}

bool FindOurPosition(ulong &tk, long &typ, double &entry, double &sl)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      tk = t;
      typ = PositionGetInteger(POSITION_TYPE);
      entry = PositionGetDouble(POSITION_PRICE_OPEN);
      sl = PositionGetDouble(POSITION_SL);
      return true;
   }
   return false;
}

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

//────────────────────────── Luu / doc SL/TP noi bo ─────────────────────────
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
   long   act = (long)FileReadNumber(h);
   double sl  = FileReadNumber(h);
   double tp  = FileReadNumber(h);
   FileClose(h);
   if(act != 0 && sl > 0 && tp > 0)
   {
      g_manActive = true; g_manSL = sl; g_manTP = tp;
      PrintFormat("[SGH] Tiep quan SL/TP noi bo: SL=%.2f TP=%.2f", g_manSL, g_manTP);
   }
}

double EffSL()
{
   return (InpHideInitialSL && g_manActive && g_manSL > 0) ? g_manSL : PositionGetDouble(POSITION_SL);
}

//────────────────────────────── Danh gia tin hieu ──────────────────────────────
bool EvalLastSignal(int &dir, double &sigClose, double &sigDist, datetime &sigTime)
{
   dir = 0; sigClose = 0.0; sigDist = 0.0; sigTime = 0;

   int lb = g_lookback; if(lb < 3) lb = 3;
   int nb = InpHistoryBars; if(nb < lb + 20) nb = lb + 20;
   int need = nb + lb + 5;

   double op[], hi[], lo[], cl[], atr[], ema[];
   datetime tm[];
   ArraySetAsSeries(op, true);  ArraySetAsSeries(hi, true);  ArraySetAsSeries(lo, true);
   ArraySetAsSeries(cl, true);  ArraySetAsSeries(atr, true); ArraySetAsSeries(ema, true);
   ArraySetAsSeries(tm, true);

   if(CopyOpen(_Symbol, InpTimeframe, 0, need, op)  <= 0) return false;
   if(CopyHigh(_Symbol, InpTimeframe, 0, need, hi)  <= 0) return false;
   if(CopyLow(_Symbol, InpTimeframe, 0, need, lo)   <= 0) return false;
   if(CopyClose(_Symbol, InpTimeframe, 0, need, cl) <= 0) return false;
   if(CopyTime(_Symbol, InpTimeframe, 0, need, tm)  <= 0) return false;
   if(g_hATR == INVALID_HANDLE || CopyBuffer(g_hATR, 0, 0, need, atr) <= 0) return false;
   if(g_hEMA == INVALID_HANDLE || CopyBuffer(g_hEMA, 0, 0, need, ema) <= 0) return false;

   // Gioi han nb theo so nen THUC TE nap duoc (tranh truy cap vuot mang)
   int got = ArraySize(cl);
   if(got < lb + 3) return false;
   nb = MathMin(nb, got - lb - 2);
   if(nb < 1) return false;

   int hr[], mn[], dw[], dy[];
   ArrayResize(hr, need); ArrayResize(mn, need); ArrayResize(dw, need); ArrayResize(dy, need);
   for(int s = 0; s < need; s++)
   {
      MqlDateTime d; TimeToStruct(tm[s], d);
      hr[s] = d.hour; mn[s] = d.min; dw[s] = d.day_of_week; dy[s] = DayId(tm[s]);
   }

   int tradesToday = 0, curDay = -1, lastS = -1, res = 0;
   for(int s = nb; s >= 1; s--)
   {
      int did = dy[s];
      if(did != curDay) { curDay = did; tradesToday = 0; }

      double a = atr[s];
      if(!(a > 0)) continue;

      // Bao ve thoi gian / news
      if(InpFridayCloseHour < 24 && dw[s] == 5 && hr[s] >= InpFridayCloseHour) continue;
      if(InNewsWindow(tm[s])) continue;

      if(g_maxTrades > 0 && tradesToday >= g_maxTrades) continue;
      if(lastS > 0 && (lastS - s) < InpMinBarsBetween) continue;

      double body = MathAbs(cl[s] - op[s]);
      if(body < g_minBodyATR * a) continue;

      bool allowBuy  = (InpTrendMode != SGH_TREND_EMA) || (ema[s] > 0 && cl[s] > ema[s]);
      bool allowSell = (InpTrendMode != SGH_TREND_EMA) || (ema[s] > 0 && cl[s] < ema[s]);

      // Kenh Donchian: dinh/day N nen TRUOC nen hien tai (shift s+1..s+lb).
      // Giong Python: nen o dau cua so (chua du `lb` nen phia truoc) -> H/L = NaN
      // nen KHONG the phat tin hieu.
      bool donchOk = (nb - s) >= lb;
      double Hv = 0.0, Lv = 0.0;
      if(donchOk)
      {
         Hv = hi[s + 1];
         Lv = lo[s + 1];
         for(int j = s + 2; j <= s + lb; j++)
         {
            if(hi[j] > Hv) Hv = hi[j];
            if(lo[j] < Lv) Lv = lo[j];
         }
      }
      double buff = InpBreakBufATR * a;

      if(donchOk && allowBuy && cl[s] >= Hv + buff)
      {
         tradesToday++; lastS = s;
         if(s == 1) { res = 1; sigClose = cl[s]; sigDist = g_slATR * a; sigTime = tm[s]; }
         continue;
      }
      if(donchOk && allowSell && cl[s] <= Lv - buff)
      {
         tradesToday++; lastS = s;
         if(s == 1) { res = -1; sigClose = cl[s]; sigDist = g_slATR * a; sigTime = tm[s]; }
      }
   }
   dir = res;
   return (dir != 0);
}

//────────────────────────────── Vao lenh ──────────────────────────────
bool OpenTrade(int dir, double sigClose, double dist, datetime sigT)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return false;
   int    dg    = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double entry = (dir > 0) ? tick.ask : tick.bid;

   double sl, tp, R;
   if(dir > 0)
   {
      sl = (InpSlFromExec ? entry : sigClose) - dist;
      R  = entry - sl;
      tp = entry + g_tpR * R;
   }
   else
   {
      sl = (InpSlFromExec ? entry : sigClose) + dist;
      R  = sl - entry;
      tp = entry - g_tpR * R;
   }
   if(!(R > 0)) return false;

   // Entry Randomizer: lech SL ngau nhien +/- N points roi tinh lai TP theo R:R
   if(InpEntryRandomPoints > 0)
   {
      double off = ((double)MathRand() / 32767.0 * 2.0 - 1.0) * InpEntryRandomPoints * point;
      sl += off;
      R = (dir > 0) ? (entry - sl) : (sl - entry);
      if(!(R > 0)) return false;
      tp = (dir > 0) ? entry + g_tpR * R : entry - g_tpR * R;
   }

   sl = NormalizeDouble(sl, dg);
   tp = NormalizeDouble(tp, dg);
   double ordSL = InpHideInitialSL ? 0.0 : sl;
   double ordTP = InpHideInitialSL ? 0.0 : tp;

   bool ok = (dir > 0)
             ? trade.Buy(InpLot, _Symbol, 0.0, ordSL, ordTP, InpComment)
             : trade.Sell(InpLot, _Symbol, 0.0, ordSL, ordTP, InpComment);
   if(!ok)
   {
      PrintFormat("[SGH] Dat lenh that bai: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return false;
   }
   PrintFormat("[SGH] EXECUTE %s | entry=%.2f | SL=%.2f | TP=%.2f | R=%.2f%s",
               (dir > 0 ? "BUY" : "SELL"), entry, sl, tp, R,
               InpHideInitialSL ? "  [an SL/TP tren san]" : "");
   if(InpHideInitialSL)
   {
      g_manSL = sl; g_manTP = tp; g_manActive = true; SaveManual();
   }
   g_lastSignalTraded = sigT;
   return true;
}

//────────────────────────── Quan ly vi the dang mo ──────────────────────────
void SyncTrackers()
{
   ulong tk; long typ; double entry, sl;
   if(!FindOurPosition(tk, typ, entry, sl))
   {
      g_posTicket = 0; g_initR = 0.0; g_beDone = false;
      g_partialDone = 0; g_trailBest = 0.0;
      return;
   }
   if(tk != g_posTicket)
   {
      g_posTicket = tk; g_beDone = false; g_partialDone = 0;
      double effSL = (InpHideInitialSL && g_manActive && g_manSL > 0) ? g_manSL : sl;
      g_initR = MathAbs(entry - effSL);
      g_trailBest = entry;
   }
}

void ManagePartial()
{
   if(g_partialFrac <= 0 || g_partialAtR <= 0) return;
   ulong tk; long typ; double entry, sl;
   if(!FindOurPosition(tk, typ, entry, sl)) return;
   if(tk == g_partialDone) return;
   double R = g_initR; if(R <= 0) return;

   MqlTick t; if(!SymbolInfoTick(_Symbol, t)) return;
   double price = (typ == POSITION_TYPE_BUY) ? t.bid : t.ask;
   double fav   = (typ == POSITION_TYPE_BUY) ? (price - entry) : (entry - price);
   if(fav < g_partialAtR * R) return;

   double vol  = PositionGetDouble(POSITION_VOLUME);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double part = MathFloor((vol * g_partialFrac) / step + 1e-9) * step;
   double rem  = NormalizeDouble(vol - part, 2);
   if(part >= vmin - 1e-9 && rem >= vmin - 1e-9)
   {
      if(trade.PositionClosePartial(tk, part, (ulong)InpMaxDevPts))
      {
         g_partialDone = tk;
         PrintFormat("[SGH] Chot mot phan %.2f lot @%.1fR", part, g_partialAtR);
      }
   }
   else g_partialDone = tk;
}

void ManageBreakEven()
{
   if(g_beAtR <= 0) return;
   ulong tk; long typ; double entry, sl;
   if(!FindOurPosition(tk, typ, entry, sl)) return;
   double R = g_initR; if(R <= 0) return;

   MqlTick t; if(!SymbolInfoTick(_Symbol, t)) return;
   double price = (typ == POSITION_TYPE_BUY) ? t.bid : t.ask;
   double fav   = (typ == POSITION_TYPE_BUY) ? (price - entry) : (entry - price);
   if(g_beDone || fav < g_beAtR * R) return;

   if(InpHideInitialSL)
   {
      if(!g_manActive) return;
      if(typ == POSITION_TYPE_BUY && entry > g_manSL)            { g_manSL = entry; SaveManual(); }
      else if(typ == POSITION_TYPE_SELL && (g_manSL <= 0 || entry < g_manSL)) { g_manSL = entry; SaveManual(); }
   }
   else
   {
      double tp = PositionGetDouble(POSITION_TP);
      double newsl = NormalizeDouble(entry, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
      trade.PositionModify(tk, newsl, tp);
   }
   g_beDone = true;
   PrintFormat("[SGH] BE@%.2fR | ticket=%I64u | entry=%.2f", g_beAtR, tk, entry);
}

void ManageTrailing()
{
   if(g_trailAtR <= 0 || g_trailGapR <= 0) return;
   ulong tk; long typ; double entry, sl;
   if(!FindOurPosition(tk, typ, entry, sl)) return;
   double R = g_initR; if(R <= 0) return;
   if(g_trailBest <= 0) g_trailBest = entry;

   MqlTick t; if(!SymbolInfoTick(_Symbol, t)) return;
   double price = (typ == POSITION_TYPE_BUY) ? t.bid : t.ask;
   double best  = (typ == POSITION_TYPE_BUY) ? MathMax(g_trailBest, price) : MathMin(g_trailBest, price);
   g_trailBest  = best;

   double exc = (typ == POSITION_TYPE_BUY) ? (best - entry) : (entry - best);
   if(exc < g_trailAtR * R) return;

   int    dg    = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double curSL = EffSL();
   if(typ == POSITION_TYPE_BUY)
   {
      double newSL = NormalizeDouble(best - g_trailGapR * R, dg);
      if(!(curSL > 0) || newSL > curSL + 1e-9)
      {
         if(InpHideInitialSL) { if(g_manActive) { g_manSL = newSL; SaveManual(); } }
         else trade.PositionModify(tk, newSL, PositionGetDouble(POSITION_TP));
         PrintFormat("[SGH] TRAIL-SL -> %.2f", newSL);
      }
   }
   else
   {
      double newSL = NormalizeDouble(best + g_trailGapR * R, dg);
      if(!(curSL > 0) || newSL < curSL - 1e-9)
      {
         if(InpHideInitialSL) { if(g_manActive) { g_manSL = newSL; SaveManual(); } }
         else trade.PositionModify(tk, newSL, PositionGetDouble(POSITION_TP));
         PrintFormat("[SGH] TRAIL-SL -> %.2f", newSL);
      }
   }
}

// Cat market khi gia cham SL/TP noi bo (chi khi an SL khoi san)
void ManageManualSLTP()
{
   if(!InpHideInitialSL) return;
   ulong tk; long typ; double entry, sl;
   bool have = FindOurPosition(tk, typ, entry, sl);
   if(have && g_manActive && g_manSL > 0 && g_manTP > 0)
   {
      MqlTick t; if(!SymbolInfoTick(_Symbol, t)) return;
      double price = (typ == POSITION_TYPE_BUY) ? t.bid : t.ask;
      bool hit = false; string why = "";
      if(typ == POSITION_TYPE_BUY)
      {
         if(price <= g_manSL)      { hit = true; why = "SL"; }
         else if(price >= g_manTP) { hit = true; why = "TP"; }
      }
      else
      {
         if(price >= g_manSL)      { hit = true; why = "SL"; }
         else if(price <= g_manTP) { hit = true; why = "TP"; }
      }
      if(hit)
      {
         if(trade.PositionClose(tk, (ulong)InpMaxDevPts))
         {
            PrintFormat("[SGH] MANUAL %s | ticket=%I64u | price=%.2f", why, tk, price);
            g_manActive = false; SaveManual();
         }
      }
   }
   else if(!have)
   {
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

//────────────────────────── Bao ve lai/lo ngay ──────────────────────────
bool DailyGuardsPassed()
{
   if(InpDailyProfitTargetPct <= 0 && InpDailyLossLimitPct <= 0 && InpEquityProtectionPct <= 0)
      return true;

   int dayId = DayId(TimeCurrent());
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(dayId != g_dayId)
   {
      g_dayId = dayId;
      g_dayStartBalance = bal;
      g_eqPeak = eq;
   }
   double base = g_dayStartBalance;
   if(base > 0)
   {
      double delta = bal - base;
      if(InpDailyLossLimitPct > 0 && delta <= -(InpDailyLossLimitPct / 100.0) * base) return false;
      if(InpDailyProfitTargetPct > 0 && delta >= (InpDailyProfitTargetPct / 100.0) * base) return false;
   }
   if(InpEquityProtectionPct > 0)
   {
      if(eq > g_eqPeak) g_eqPeak = eq;
      if(eq <= g_eqPeak * (1.0 - InpEquityProtectionPct / 100.0)) return false;
   }
   return true;
}

//────────────────────────────── Events ──────────────────────────────
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpMaxDevPts);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   ApplyProfile();
   ParseNews();

   g_hATR = iATR(_Symbol, InpTimeframe, 14);
   if(g_hATR == INVALID_HANDLE) { Print("[SGH] Khong tao duoc ATR handle"); return INIT_FAILED; }
   if(InpTrendMode == SGH_TREND_EMA)
   {
      g_hEMA = iMA(_Symbol, InpTimeframe, InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(g_hEMA == INVALID_HANDLE) { Print("[SGH] Khong tao duoc EMA handle"); return INIT_FAILED; }
   }

   LoadManual();
   EventSetMillisecondTimer(InpPollMs);
   PrintFormat("[SGH] Khoi dong %s | profile=%d | lot=%.2f | lb=%d | body=%.2f | SL=%.2fATR | TP=%.2fR | max=%d/ngay | BE=%.1fR | trail=%.1fR/%.1fR | hideSL=%s",
               _Symbol, (int)InpProfile, InpLot, g_lookback, g_minBodyATR, g_slATR, g_tpR, g_maxTrades,
               g_beAtR, g_trailAtR, g_trailGapR, InpHideInitialSL ? "ON" : "OFF");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(InpHideInitialSL) SaveManual();
   if(g_hATR != INVALID_HANDLE) IndicatorRelease(g_hATR);
   if(g_hEMA != INVALID_HANDLE) IndicatorRelease(g_hEMA);
}

void OnTimer()
{
   // Kiem tra SL/TP noi bo + quan ly vi the moi lan poll (ke ca trong nen)
   ManageManualSLTP();
   SyncTrackers();
   ManagePartial();
   ManageBreakEven();
   ManageTrailing();

   // Chi xu ly tin hieu khi co nen moi dong
   datetime t1 = iTime(_Symbol, InpTimeframe, 1);
   if(t1 == 0 || t1 == g_lastBar) return;
   g_lastBar = t1;

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return;
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != (long)SYMBOL_TRADE_MODE_FULL) return;
   if(HasOurPositionOrPending()) return;

   // Loc spread
   int sp = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(InpMaxSpreadPoints > 0 && sp > InpMaxSpreadPoints)
   {
      PrintFormat("[SGH] spread %d > %d -> bo qua vao lenh", sp, InpMaxSpreadPoints);
      return;
   }

   // Gioi han lai/lo ngay + equity protection
   if(!DailyGuardsPassed()) return;

   int dir; double sigClose, dist; datetime sigT;
   if(!EvalLastSignal(dir, sigClose, dist, sigT)) return;
   if(g_lastSignalTraded == sigT) return;   // da vao lenh cho nen tin hieu nay
   OpenTrade(dir, sigClose, dist, sigT);
}

void OnTick() { }   // xu ly trong OnTimer
//+------------------------------------------------------------------+