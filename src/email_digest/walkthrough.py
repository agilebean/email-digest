"""Topic-scoped terminal walkthrough: review digest sources, update keep list, optionally unsubscribe."""

from __future__ import annotations

import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date
from pathlib import Path

from unsubscribe.classifier import is_digest_source_candidate
from unsubscribe.execution import (
    debugger_address_from_env,
    print_unsubscribe_report,
    run_automated_unsubscribe,
)
from unsubscribe.gmail_facade import GmailFacade, headers_from_summary
from unsubscribe.keep_list import (
    add_to_keep_list,
    add_to_unsubscribed_list,
    is_kept,
    is_unsubscribed,
    load_keep_list,
    load_unsubscribed_list,
)

from email_digest.config import TopicConfig
from email_digest.gmail_query import build_digest_gmail_query

_BODY_PREFETCH_WORKERS = 8
_PREVIEW_WIDTH = 72
_PREVIEW_MAX_LINES = 5


def _body_preview_lines(
    text: str,
    *,
    width: int = _PREVIEW_WIDTH,
    max_lines: int = _PREVIEW_MAX_LINES,
) -> str:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        cand = " ".join(cur + [w])
        if len(cand) > width and cur:
            lines.append(" ".join(cur))
            cur = [w]
            if len(lines) >= max_lines:
                break
        else:
            cur.append(w)
    if len(lines) < max_lines and cur:
        lines.append(" ".join(cur))
    return "\n".join(lines[:max_lines])


def _fetch_one_body_plain(facade: GmailFacade, message_id: str) -> str:
    try:
        return facade.get_message_body_text(message_id)
    except Exception as e:
        print(f"(Could not load body for {message_id}: {e})", file=sys.stderr, flush=True)
        return ""


def _start_body_prefetch(
    facade: GmailFacade,
    messages: list,
) -> tuple[ThreadPoolExecutor, dict[str, Future[str]]]:
    n = len(messages)
    workers = min(_BODY_PREFETCH_WORKERS, max(1, n))
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[str, Future[str]] = {
        m.id: executor.submit(_fetch_one_body_plain, facade, m.id) for m in messages
    }
    return executor, futures


def _prompt_sources(prompt: str, *, input_fn: Callable[[str], str]) -> str:
    """Return ``''`` (keep), ``'u'`` (unsubscribe), ``'s'`` (skip), or ``'q'`` (quit)."""
    while True:
        raw = input_fn(prompt)
        s = raw.strip().lower()
        if s == "":
            return ""
        if s == "u":
            return "u"
        if s == "s":
            return "s"
        if s == "q":
            return "q"
        print("  (Enter = keep, u = unsubscribe, s = skip, q = quit — try again.)", flush=True)


def _print_selection_summary(
    num_unsub: int,
    num_kept: int,
) -> None:
    """Print summary of user selections from the walkthrough."""
    print()
    if num_unsub:
        print(f"Selected for unsubscribe: {num_unsub}")
    if num_kept:
        print(f"Added to keep list: {num_kept}")
    if num_unsub == 0 and num_kept == 0:
        print("No selections made.")
    print()


def _format_sources_table(
    messages: list,
    keep_data: dict,
    *,
    unsubscribed_data: dict | None = None,
    from_width: int = 36,
    preview_width: int = 64,
) -> str:
    """Format messages as a human-readable aligned table."""
    unsub = unsubscribed_data or {}
    rows: list[tuple[int, str, str, str]] = []
    for i, m in enumerate(messages, start=1):
        if is_kept(keep_data, m.from_):
            status = "kept"
        elif is_unsubscribed(unsub, m.from_):
            status = "unsub'd"
        else:
            status = "new"
        snip = (m.snippet or "").replace("\n", " ").strip()[:preview_width - 10]
        preview = f"{m.subject!r}"
        if snip:
            preview += f" :: {snip}"
        rows.append((i, status, m.from_, preview))

    num_w = max(len(str(rows[-1][0])), 2) if rows else 2
    status_w = max(len(r[1]) for r in rows) if rows else 6

    header = f"  {'#':>{num_w}}  {'Status':<{status_w}}  {'From':<{from_width}}  Subject / Preview"
    sep = "  " + "─" * (len(header) - 2)
    lines = [header, sep]

    for num, status, from_, preview in rows:
        from_d = from_[:from_width]
        prev_d = preview[:preview_width]
        lines.append(
            f"  {num:>{num_w}}  {status:<{status_w}}  {from_d:<{from_width}}  {prev_d}"
        )
    return "\n".join(lines)


def run_digest_sources(
    cfg: TopicConfig,
    topic_path: Path,
    facade: GmailFacade,
    keep_list_path: Path,
    *,
    since: date | None,
    max_results: int,
    input_fn: Callable[[str], str],
    body: bool = False,
    new_only: bool = False,
) -> int:
    """Interactive review of digest-source candidates for one topic.

    Per candidate: **Enter** = keep (add to keep list), **u** = unsubscribe,
    **s** = skip, **q** = quit walkthrough.  At the end, selected unsubscribe
    items are sent through automated unsubscribe.

    When *new_only* is True, only candidates not already on the keep list are shown.
    When *body* is True, plain-text bodies are prefetched in parallel and shown as a preview.

    Returns ``0`` on normal completion, ``1`` if Gmail list fails, ``130`` on interrupt.
    """
    query = build_digest_gmail_query(
        window_days=cfg.window_days,
        senders=list(cfg.senders),
        keywords=list(cfg.keywords),
        folders=list(cfg.folders),
        since=since,
    )
    print(
        f"Digest sources — topic {cfg.name!r} ({topic_path.name})",
        flush=True,
    )
    try:
        all_messages = facade.list_messages(query, max_results=max_results)
    except Exception as e:
        print(f"Could not list messages: {e}", file=sys.stderr, flush=True)
        return 1

    # Classify each message
    candidates: list = []
    non_candidates: list = []
    keep_data = load_keep_list(keep_list_path)
    unsub_data = load_unsubscribed_list()
    for m in all_messages:
        h = headers_from_summary(m)
        if is_digest_source_candidate(h):
            candidates.append(m)
        else:
            non_candidates.append(m)

    # Summary
    n_total = len(all_messages)
    n_cand = len(candidates)
    n_new = sum(1 for m in candidates if not is_kept(keep_data, m.from_))
    num_kept_cand = n_cand - n_new
    msg_s = "message" if n_total == 1 else "messages"
    cand_s = "candidate" if n_cand == 1 else "candidates"
    print(
        f"  {n_total} {msg_s} total, {n_cand} {cand_s} — "
        f"{n_new} new, {num_kept_cand} already kept"
    )
    if non_candidates:
        nc_s = "message" if len(non_candidates) == 1 else "messages"
        print(f"  ({len(non_candidates)} non-candidate {nc_s} omitted)")
    print(flush=True)

    # Split into kept and new
    kept_candidates = [m for m in candidates if is_kept(keep_data, m.from_)]
    new_candidates = [m for m in candidates if not is_kept(keep_data, m.from_)]

    # Show kept sources in a compact table (no walkthrough)
    if kept_candidates and not new_only:
        print(f"\nAlready kept ({len(kept_candidates)}):")
        print(_format_sources_table(kept_candidates, keep_data, unsubscribed_data=unsub_data))

    # Decide which to walk through
    if new_only or not kept_candidates:
        walk = new_candidates
    else:
        walk = new_candidates

    if not walk:
        if kept_candidates:
            print(f"\nAll {len(kept_candidates)} sources already kept — nothing new to review.", flush=True)
        else:
            print("No candidates to review.", flush=True)
        return 0

    # Print table of new sources
    label = "New" if kept_candidates else "To review"
    src_s = "source" if len(walk) == 1 else "sources"
    print(f"\n{label} ({len(walk)} {src_s}):")
    print(_format_sources_table(walk, keep_data, unsubscribed_data=unsub_data))
    print(flush=True)

    # Prefetch bodies in parallel if requested (new candidates only)
    body_pool: ThreadPoolExecutor | None = None
    body_futures: dict[str, Future[str]] = {}
    if body:
        if walk:
            body_pool, body_futures = _start_body_prefetch(facade, walk)

    unsub_selected: list = []
    num_kept = 0

    try:
        for i, m in enumerate(walk, start=1):
            # Reload keep list each iteration — a previous [Enter] may have
            # added this sender, making this message now kept.
            keep_data = load_keep_list(keep_list_path)
            if is_kept(keep_data, m.from_):
                print(
                    f"\n{'─' * 60}\n  #{i}  (now kept — added in a previous step)\n"
                    f"  From: {m.from_}\n  Subject: {m.subject!r}\n",
                    flush=True,
                )
                continue
            print(
                f"\n{'─' * 60}\n  #{i}  From: {m.from_}\n"
                f"  Subject: {m.subject!r}\n  Date: {m.date}\n",
                end="",
                flush=True,
            )
            if body and m.id in body_futures:
                body_text = body_futures[m.id].result()
                if body_text:
                    print(_body_preview_lines(body_text), flush=True)
                else:
                    print("(no preview)", flush=True)
                print(flush=True)
            action = _prompt_sources(
                "  [Enter] keep as source  [u] unsubscribe  [s] skip  [q] quit\n  > ",
                input_fn=input_fn,
            )
            if action == "":
                add_to_keep_list(keep_list_path, m.from_, m.subject)
                num_kept += 1
                print("  (added to keep list.)", flush=True)
            elif action == "u":
                unsub_selected.append(m)
                print("  (marked for unsubscribe.)", flush=True)
            elif action == "s":
                print("  (skipped.)", flush=True)
            else:  # q
                print(
                    "\n(Stopping walkthrough early; prior selections are already saved.)",
                    flush=True,
                )
                break
    except KeyboardInterrupt:
        print("\nInterrupted. Partial selections:", flush=True)
        _print_selection_summary(len(unsub_selected), num_kept)
        return 130
    finally:
        if body_pool is not None:
            body_pool.shutdown(wait=False, cancel_futures=True)

    _print_selection_summary(len(unsub_selected), num_kept)

    # Automated unsubscribe for selected items
    if unsub_selected:
        while True:
            raw = input_fn(
                f"Press Enter to unsubscribe all {len(unsub_selected)} selected "
                "[q to quit]\n  > "
            )
            choice = raw.strip()
            if choice.lower() == "q":
                return 0
            if choice == "":
                dbg = debugger_address_from_env()
                selected_items = [(None, m) for m in unsub_selected]
                report = run_automated_unsubscribe(
                    facade,
                    selected_items,
                    debugger_address=dbg,
                )
                print_unsubscribe_report(report)
                # Record unsubscribed senders for future runs
                for r in report:
                    if r.get("status") in ("confirmed", "server-acknowledged"):
                        add_to_unsubscribed_list(
                            None, r.get("sender", ""), r.get("subject", "")
                        )
                break
            print("  (Enter or q — try again.)")

    print("\nDone.", flush=True)
    return 0
