"""이메일 다이제스트 생성 및 발송.

이메일 클라이언트는 <style> 블록을 자주 무시하므로 스타일은 전부 인라인입니다.
"""
from __future__ import annotations

import json
import logging
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from html import escape
from pathlib import Path

log = logging.getLogger("monitor.digest")

INK, MUTED, SEAL, RULE = "#131E1D", "#5C6B69", "#A81E17", "#CBD4D1"
SERIF = "'Nanum Myeongjo','Batang',Georgia,serif"
SANS = "'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif"
MONO = "'IBM Plex Mono',Menlo,Consolas,monospace"

LEVEL_LABEL = {"urgent": "조치 필요", "review": "검토", "none": "참고"}
WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]


def dday(effective: str, today: date) -> str:
    if not effective:
        return ""
    try:
        d = (datetime.strptime(effective, "%Y-%m-%d").date() - today).days
    except ValueError:
        return ""
    if d < 0:
        return f"시행 {-d}일 경과"
    if d == 0:
        return "오늘 시행"
    return f"D-{d}"


def _diff_html(it: dict) -> str:
    """신구조문 대비표. 메일 클라이언트를 고려해 테이블 2열로 그립니다."""
    raw = it.get("diff") or ""
    try:
        pairs = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return ""
    if not pairs:
        return ""

    rows = ""
    for pair in pairs[:4]:          # 메일에는 앞 4개 조문만
        old = escape(pair.get("old") or "(신설)")[:400]
        new = escape(pair.get("new") or "(삭제)")[:400]
        art = escape(pair.get("article") or "")
        rows += f"""
          <tr><td colspan="2" style="font-family:{MONO};font-size:10px;color:{MUTED};padding:9px 0 4px">{art}</td></tr>
          <tr>
            <td width="50%" valign="top" style="padding:0 8px 10px 0;font-family:{SANS};font-size:12px;line-height:1.6;color:{MUTED}">{old}</td>
            <td width="50%" valign="top" style="padding:0 0 10px 8px;font-family:{SANS};font-size:12px;line-height:1.6;color:{INK};border-left:1px solid {RULE};padding-left:10px">{new}</td>
          </tr>"""

    more = len(pairs) - 4
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;border-top:1px solid {RULE};padding-top:8px">
        <tr>
          <td width="50%" style="font-family:{MONO};font-size:9.5px;letter-spacing:.1em;color:{MUTED};padding-bottom:4px">개정 전</td>
          <td width="50%" style="font-family:{MONO};font-size:9.5px;letter-spacing:.1em;color:{SEAL};padding-bottom:4px;padding-left:10px">개정 후</td>
        </tr>{rows}
        {f'<tr><td colspan="2" style="font-family:{MONO};font-size:10px;color:{MUTED}">외 {more}개 조문 — 대시보드에서 전체 보기</td></tr>' if more > 0 else ''}
      </table>"""


def _item_html(it: dict, today: date, accent: str) -> str:
    eff, dd = it.get("effective_date", ""), dday(it.get("effective_date", ""), today)
    meta = " · ".join(
        x for x in [it.get("kind", ""), f"시행 {eff.replace('-', '.')}" if eff else "", dd] if x
    )
    scope = it.get("scope", "")
    return f"""
  <tr><td style="padding:0 0 18px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="3" style="background:{accent};font-size:0;line-height:0">&nbsp;</td>
        <td style="padding-left:14px">
          <div style="font-family:{SERIF};font-size:16px;font-weight:700;color:{INK};line-height:1.45;margin-bottom:5px">{escape(it.get('title',''))}</div>
          <div style="font-family:{MONO};font-size:11px;color:{MUTED};letter-spacing:.03em;margin-bottom:7px">{escape(meta)}</div>
          <div style="font-family:{SANS};font-size:13.5px;color:#2C3A38;line-height:1.65">{escape(it.get('summary','')) or f'<span style="color:{MUTED}">자동 요약을 사용하지 않는 상태입니다. 원문을 확인하세요.</span>'}</div>
          {f'<div style="font-family:{SANS};font-size:12.5px;color:{MUTED};line-height:1.6;margin-top:6px">적용 대상 — {escape(scope)}</div>' if scope else ''}
          {_diff_html(it)}
          <a href="{escape(it.get('link','#'))}" style="font-family:{MONO};font-size:11px;color:{SEAL};text-decoration:none;display:inline-block;margin-top:8px">{escape(it.get('source','원문'))} 원문 보기 &rarr;</a>
        </td>
      </tr>
    </table>
  </td></tr>"""


def _section(title: str, items: list[dict], today: date, accent: str, color: str) -> str:
    if not items:
        return ""
    rows = "".join(_item_html(i, today, accent) for i in items)
    return f"""
  <tr><td style="padding:26px 0 12px">
    <div style="font-family:{MONO};font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:{color}">{title} {len(items)}건</div>
  </td></tr>
  {rows}"""


def build_html(items: list[dict], today: date, issue: int, dashboard_url: str = "#") -> str:
    urgent = [i for i in items if i.get("action_level") == "urgent"]
    review = [i for i in items if i.get("action_level") == "review"]
    info = [i for i in items if i.get("action_level") == "none"]
    stamp = f"{today.year}.{today.month:02d}.{today.day:02d} ({WEEKDAY[today.weekday()]})"

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#E9ECEA">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#E9ECEA">
<tr><td align="center" style="padding:24px 12px">
  <table role="presentation" width="620" cellpadding="0" cellspacing="0" border="0" style="max-width:620px;width:100%;background:#FFFFFF">
    <tr><td style="padding:32px 34px">

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td style="border-bottom:2px solid {INK};padding-bottom:12px">
          <div style="font-family:{SERIF};font-size:23px;font-weight:800;color:{INK};line-height:1.2">노동법 모니터 · 제{issue}호</div>
          <div style="font-family:{MONO};font-size:11px;color:{MUTED};letter-spacing:.05em;margin-top:6px">{stamp} · 신규 {len(items)}건 · 조치 필요 {len(urgent)}건</div>
        </td></tr>
        {_section("조치 필요", urgent, today, SEAL, SEAL)}
        {_section("검토", review, today, "#8A5A12", MUTED)}
        {_section("참고", info, today, RULE, MUTED)}
        <tr><td style="border-top:1px solid {RULE};padding-top:16px;margin-top:8px">
          <div style="font-family:{MONO};font-size:10.5px;color:{MUTED};line-height:1.8">
            AI가 원문을 요약했습니다. 판단 전 반드시 원문을 확인하세요.<br>
            조치 필요 항목은 대시보드에서 담당자를 지정해 주세요.<br>
            <a href="{escape(dashboard_url)}" style="color:{SEAL};text-decoration:none">대시보드 열기 &rarr;</a>
          </div>
        </td></tr>
      </table>

    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def build_text(items: list[dict], today: date) -> str:
    lines = [f"노동법 모니터 {today.isoformat()} · 신규 {len(items)}건", ""]
    for level in ("urgent", "review", "none"):
        group = [i for i in items if i.get("action_level") == level]
        if not group:
            continue
        lines.append(f"── {LEVEL_LABEL[level]} {len(group)}건 ──")
        for i in group:
            eff = i.get("effective_date", "")
            lines += [
                f"· {i.get('title','')}",
                f"  {i.get('kind','')}{f' / 시행 {eff} {dday(eff, today)}' if eff else ''}",
                f"  {i.get('summary','')}",
                *( [f"  [신구조문 대비 {len(json.loads(i['diff']))}개 조문 — 대시보드 참조]"]
                   if i.get("diff") else [] ),
                f"  {i.get('link','')}",
                "",
            ]
    lines.append("AI 요약입니다. 판단 전 원문을 확인하세요.")
    return "\n".join(lines)


def send(html: str, text: str, subject: str, cfg: dict, smtp: dict) -> None:
    name, addr = parseaddr(cfg["from"])
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((name or "노동법 모니터", addr))
    msg["To"] = ", ".join(cfg["to"])
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    port = int(smtp.get("port", 587))
    host = smtp["host"]
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls()
    with server:
        if smtp.get("user"):
            server.login(smtp["user"], smtp["password"])
        server.sendmail(addr, cfg["to"], msg.as_string())
    log.info("메일 발송 완료 → %s", ", ".join(cfg["to"]))


def save_preview(html: str, path: str | Path = "data/preview.html") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p
