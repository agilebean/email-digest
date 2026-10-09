"""After interactive check: one-click POST, body link extraction, then browser batch."""

from __future__ import annotations

import os
import re
import time
from typing import Any, cast
from urllib.parse import urlparse

from unsubscribe.browser_unsubscribe import (
    BrowserUnsubscribeJob,
    batch_browser_unsubscribe,
)
from unsubscribe.gmail_facade import GmailFacade, GmailHeaderSummary, headers_from_summary
from unsubscribe.timed_run import TimedRun, format_progress_line
from unsubscribe.unsubscribe_link import NoUnsubscribeLinkError, UnsafeLinkError, extract_unsubscribe_link
from unsubscribe.unsubscribe_oneclick import (
    list_unsubscribe_http_get_url,
    NoUnsubscribeHeaderError,
    UnsubscribeNotOneClickError,
    UnsubscribePostRedirectError,
    try_one_click_unsubscribe,
)

SelectedItem = tuple[int | None, GmailHeaderSummary]


def _result_row(
    email_index: int | None,
    m: GmailHeaderSummary,
    *,
    method: str,
    status: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "email_index": email_index,
        "subject": m.subject,
        "sender": m.from_,
        "method": method,
        "status": status,
        "detail": detail,
    }


def _one_click_http_code(out: str) -> str:
    m = re.search(r"HTTP\s+(\d+)", out, re.I)
    return m.group(1) if m else "?"


# HTTP 202 Accepted: request recorded but processing is incomplete from the client's perspective.
# Many ESPs return 202 while the visible unsubscribe still requires a GET (browser) or a vendor page.
_ONE_CLICK_CODES_NEED_BROWSER_FOLLOWUP = frozenset({"202"})


def _one_click_needs_browser_followup(one_click_message: str) -> bool:
    code = _one_click_http_code(one_click_message)
    return code in _ONE_CLICK_CODES_NEED_BROWSER_FOLLOWUP


def _append_browser_detail_preamble(preamble: str | None, detail: str) -> str:
    p = (preamble or "").strip()
    if not p:
        return detail
    return f"{p} Then: {detail}"


def print_unsubscribe_report(results: list[dict[str, Any]]) -> None:
    """Truthful per-email report: never equate POST 2xx with completed unsubscribe."""
    print("\n  ── Results ──\n")
    capture_paths: set[str] = set()
    for r in results:
        idx = r.get("email_index")
        subj = r.get("subject", "")
        sender = r.get("sender", "")
        method = r.get("method", "")
        status = r.get("status", "")
        detail = str(r.get("detail", ""))
        page_snippet = str(r.get("page_text_snippet", ""))
        capture_path = str(r.get("capture_session_path", ""))

        if idx is not None:
            head = f'   #{idx}  "{subj}" — {sender}'
        else:
            head = f'   "{subj}" — {sender}'
        print(head)

        if method == "one-click" and status == "server-acknowledged":
            code = _one_click_http_code(detail)
            print(f"       one-click POST → server accepted ({code})")
        elif method == "browser" and status == "confirmed":
            print(f"       {detail}")
        elif method == "browser" and status == "clicked-no-confirmation":
            print(f"       {detail}")
        elif method == "browser" and status == "failed":
            print(f"       {detail}")
        else:
            # mailto, none, skipped browser, etc.
            print(f"       {detail}")

        if page_snippet:
            print(f"       page text: {page_snippet}")

        if capture_path:
            capture_paths.add(capture_path)

        print()

    n = len(results)
    n_conf = sum(1 for r in results if r.get("status") == "confirmed")
    n_ack = sum(1 for r in results if r.get("status") == "server-acknowledged")
    n_noconf = sum(1 for r in results if r.get("status") == "clicked-no-confirmation")
    n_fail = sum(1 for r in results if r.get("status") == "failed")

    parts: list[str] = []
    if n_conf:
        parts.append(f"{n_conf} confirmed")
    if n_ack:
        parts.append(f"{n_ack} server-acknowledged")
    if n_noconf:
        parts.append(f"{n_noconf} clicked (no confirmation on page)")
    if n_fail:
        parts.append(f"{n_fail} failed")
    summary = ", ".join(parts) if parts else "no attempts"
    print(f"   ── {n} attempted: {summary} ──")
    if capture_paths:
        for cp in sorted(capture_paths):
            print(f"   Page captures: {cp}")
    print()


def debugger_address_from_env() -> str | None:
    """Same debugger env as googleads-invoice-glugglejug (one Brave, one port)."""
    raw = (os.environ.get("GOOGLEADS_BROWSER_DEBUGGER_ADDRESS") or "").strip()
    return raw or None


def subscriber_email_for_browser_from_env() -> str | None:
    """When set, overrides per-message ``delivered_to`` for ``type=email`` form prefills."""
    raw = (os.environ.get("UNSUBSCRIBE_SUBSCRIBER_EMAIL") or "").strip()
    return raw or None


def _subject_preview(subj: str, max_len: int = 52) -> str:
    s = subj.replace("\n", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def run_automated_unsubscribe(
    facade: GmailFacade,
    selected: list[SelectedItem],
    *,
    debugger_address: str | None,
    timeout_per_url_s: float = 30,
    verbose: bool = True,
    mirror_failure_trace: bool = True,
) -> list[dict[str, Any]]:
    """
    For each selected message: RFC 8058 one-click first, then allowlisted body link + browser.

    ``selected`` is ``(walkthrough_index | None, message)`` — use ``None`` for re-check picks.

    One-click **HTTP 202** is treated as incomplete: the run still resolves an unsubscribe URL
    (body or ``List-Unsubscribe`` GET) and queues the browser pass when possible.

    Returns one **truthful** result dict per message (POST 2xx ⇒ ``server-acknowledged``, not unsubscribed).

    ``mirror_failure_trace`` (default ``True``) is passed to :func:`batch_browser_unsubscribe` for
    optional Downloads mirrors on browser failures.
    """
    nsel = len(selected)
    if nsel == 0:
        return []

    t0 = time.monotonic()
    results: list[dict[str, Any] | None] = [None] * nsel
    browser_jobs: list[tuple[int, BrowserUnsubscribeJob]] = []
    browser_one_click_preamble: dict[int, str] = {}

    if verbose:
        print("", flush=True)

    for pos, (email_index, m) in enumerate(selected):
        headers = headers_from_summary(m)
        mailto_hint = ""

        try:
            out = try_one_click_unsubscribe(headers)
            if out.startswith("One-Click unsubscribe accepted"):
                code = _one_click_http_code(out)
                if _one_click_needs_browser_followup(out):
                    browser_one_click_preamble[pos] = out.strip()
                    if verbose:
                        sq = _subject_preview(m.subject)
                        print(
                            f"    [{pos + 1}/{nsel}] {m.from_} — \"{sq}\" — "
                            f"one-click POST returned HTTP {code}; "
                            "continuing with browser if an unsubscribe URL is available.",
                            flush=True,
                        )
                else:
                    results[pos] = _result_row(
                        email_index,
                        m,
                        method="one-click",
                        status="server-acknowledged",
                        detail=out.strip(),
                    )
                    if verbose:
                        sq = _subject_preview(m.subject)
                        print(
                            f"    [{pos + 1}/{nsel}] {m.from_} — \"{sq}\" — "
                            f"one-click POST accepted (HTTP {code}); not the same as "
                            "finished unsubscribe.",
                            flush=True,
                        )
                    continue
            low = out.lower()
            if low.startswith("manual action"):
                mailto_hint = out[:300]
        except (
            NoUnsubscribeHeaderError,
            UnsubscribeNotOneClickError,
            UnsubscribePostRedirectError,
        ):
            pass
        except OSError:
            pass
        except Exception:
            pass

        body_url: str | None = None
        extract_err = ""
        queued_from_list_header = False
        try:
            html = facade.get_message_html(m.id)
            body_url = extract_unsubscribe_link(html)
        except (NoUnsubscribeLinkError, UnsafeLinkError) as e:
            extract_err = str(e) or type(e).__name__
        except Exception as e:
            extract_err = str(e) or type(e).__name__

        if not body_url:
            header_url = list_unsubscribe_http_get_url(headers)
            if header_url:
                body_url = header_url
                queued_from_list_header = True

        if not body_url:
            if mailto_hint:
                detail = mailto_hint
                if extract_err:
                    detail = f"{mailto_hint} Body: {extract_err}"
                detail = f"mailto / manual only — {detail[:280]}"
            else:
                detail = (
                    extract_err
                    or "No unsubscribe URL from message body or List-Unsubscribe header."
                )
            detail = _append_browser_detail_preamble(
                browser_one_click_preamble.get(pos), detail
            )
            results[pos] = _result_row(
                email_index,
                m,
                method="none",
                status="failed",
                detail=detail,
            )
            if verbose:
                sq = _subject_preview(m.subject)
                tag = "mailto / manual" if mailto_hint else "no automated path"
                print(
                    f"    [{pos + 1}/{nsel}] {m.from_} — \"{sq}\" — {tag}.",
                    flush=True,
                )
            continue

        job: BrowserUnsubscribeJob = (
            email_index,
            m.subject,
            m.from_,
            body_url,
            m.delivered_to,
        )
        browser_jobs.append((pos, job))
        if verbose:
            host = urlparse(body_url).hostname or body_url[:48]
            sq = _subject_preview(m.subject)
            src = "List-Unsubscribe header" if queued_from_list_header else "body link"
            print(
                f"    [{pos + 1}/{nsel}] {m.from_} — \"{sq}\" — "
                f"queued for Brave — {host} ({src}).",
                flush=True,
            )

    elapsed_analysis = time.monotonic() - t0
    nb = len(browser_jobs)

    if nb == 0:
        if verbose:
            print(
                format_progress_line(
                    1,
                    1,
                    elapsed_analysis,
                    elapsed_analysis,
                    "Done — no browser URLs queued.",
                ),
                flush=True,
            )
            if debugger_address:
                print(
                    "    (Page capture runs only with the Brave batch; this run had no messages "
                    "that needed a browser URL after one-click, body link, and List-Unsubscribe fallback.)\n",
                    flush=True,
                )
        return cast(list[dict[str, Any]], results)

    if not debugger_address:
        total_steps = 2
    else:
        total_steps = 1 + 2 + 2 * nb

    tr = TimedRun(total_steps, enabled=verbose)
    tr.t0 = t0
    tr.last = time.monotonic()
    tr.n = 2

    if verbose:
        if debugger_address:
            tail = (
                f"Message analysis complete; {nb} URL(s) queued for Brave "
                f"(GOOGLEADS_BROWSER_DEBUGGER_ADDRESS)."
            )
        else:
            tail = (
                f"Message analysis complete; {nb} URL(s) need Brave but "
                f"debugger address is unset (browser phase skipped)."
            )
        print(
            format_progress_line(1, total_steps, elapsed_analysis, elapsed_analysis, tail),
            flush=True,
        )

    if not debugger_address:
        for pos, job in browser_jobs:
            _email_index, _sj, _snd, url, _hint = job
            m = selected[pos][1]
            fail_detail = (
                "browser → ✗ not run: set GOOGLEADS_BROWSER_DEBUGGER_ADDRESS and start Brave "
                f"with --remote-debugging-port. URL was: {url[:120]}"
            )
            fail_detail = _append_browser_detail_preamble(
                browser_one_click_preamble.get(pos), fail_detail
            )
            results[pos] = _result_row(
                selected[pos][0],
                m,
                method="browser",
                status="failed",
                detail=fail_detail,
            )
        tr.step(
            f"Browser automation skipped ({nb} URL(s)); Brave was not contacted."
        )
    else:
        job_list = [j for _, j in browser_jobs]
        batch_rows = batch_browser_unsubscribe(
            job_list,
            debugger_address=debugger_address,
            timeout_per_url_s=timeout_per_url_s,
            subscriber_email=subscriber_email_for_browser_from_env(),
            progress=tr,
            mirror_failure_trace=mirror_failure_trace,
        )
        for (pos, _job), row in zip(browser_jobs, batch_rows, strict=True):
            out_row = dict(row)
            out_row["detail"] = _append_browser_detail_preamble(
                browser_one_click_preamble.get(pos),
                str(out_row.get("detail", "")),
            )
            results[pos] = out_row

    assert all(x is not None for x in results)
    return cast(list[dict[str, Any]], results)
