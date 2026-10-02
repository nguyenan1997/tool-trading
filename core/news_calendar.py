"""
core/news_calendar.py
Lịch kinh tế (Forex Factory, miễn phí, không cần API key).

- Tải lịch tuần này + tuần sau, cache lại (mặc định 6h).
- Quy đổi giờ sự kiện sang GIỜ VIỆT NAM (UTC+7).
- Cung cấp in_window(now_vn, currencies, min_impact, before_min, after_min)
  để biết "hiện tại có nằm trong khoảng tin quan trọng không".
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
_IMPACTS = {"High": 3, "Medium": 2, "Low": 1, "Holiday": 0, "": 0}


class NewsCalendar:
    def __init__(self):
        self._lock = threading.RLock()
        self._events = []        # list[(vn_dt, country, impact, title)]
        self._fetched_at = 0.0
        self._refresh_sec = 6 * 3600
        self._last_error = None

    def _fetch(self, url):
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))

    def refresh(self, force: bool = False, refresh_hours: float = None):
        if refresh_hours:
            self._refresh_sec = max(0.5, refresh_hours) * 3600
        with self._lock:
            if not force and self._events and (time.time() - self._fetched_at) < self._refresh_sec:
                return
            evs = []
            ok = False
            for url in _URLS:
                try:
                    for e in self._fetch(url):
                        try:
                            dt = datetime.fromisoformat(e["date"])
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            vn = dt.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(hours=7)
                            evs.append((vn, e.get("country", ""), e.get("impact", ""), e.get("title", "")))
                        except Exception:
                            continue
                    ok = True
                except Exception as ex:
                    self._last_error = str(ex)
                    logger.debug(f"[NEWS] fetch skipped {url}: {ex}")
            if evs:
                self._events = sorted(evs)
                self._fetched_at = time.time()
                logger.info(f"[NEWS] loaded {len(evs)} events (VN times)")

    def in_window(self, now_vn: datetime, currencies=None, min_impact="High",
                  before_min=15, after_min=15, refresh_hours=6.0):
        """Trả về (True, event) nếu có tin >= min_impact của `currencies`
        trong [now-before, now+after] (giờ VN). Nếu chưa tải được -> (False, None)."""
        try:
            self.refresh(refresh_hours=refresh_hours)
        except Exception:
            pass
        if not self._events:
            return False, None
        want = _IMPACTS.get(min_impact, 3)
        cur_set = set(currencies) if currencies else None
        lo = now_vn - timedelta(minutes=before_min)
        hi = now_vn + timedelta(minutes=after_min)
        for ev in self._events:
            dt, cc, imp, title = ev
            if cur_set and cc not in cur_set:
                continue
            if _IMPACTS.get(imp, 0) < want:
                continue
            if lo <= dt <= hi:
                return True, ev
        return False, None

    def upcoming(self, now_vn: datetime, currencies=None, min_impact="High", within_hours=24):
        """Danh sách tin sắp tới (để log)."""
        want = _IMPACTS.get(min_impact, 3)
        cur_set = set(currencies) if currencies else None
        out = []
        for dt, cc, imp, title in self._events:
            if cur_set and cc not in cur_set:
                continue
            if _IMPACTS.get(imp, 0) < want:
                continue
            if now_vn <= dt <= now_vn + timedelta(hours=within_hours):
                out.append((dt, cc, imp, title))
        return out


news_calendar = NewsCalendar()
