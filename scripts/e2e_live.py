"""End-to-end smoke test against a deployed server.

Covers: admin key -> create competition -> add question/choices -> join two
players -> concurrent WebSocket channels (admin + 2 participants) -> start ->
launch question -> answer -> leaderboard -> finish -> delete (cascade cleanup).

Usage:
    python scripts/e2e_live.py [base_url]
    python scripts/e2e_live.py --cleanup-all https://...
    python scripts/e2e_live.py --password mohamed [--wait SECS]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request

import websockets

DEFAULT_BASE = "https://quran-quiz-wun0.onrender.com"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        sys.exit(1)


def _request(base: str, method: str, path: str, body=None, token=None) -> dict:
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {raw}")


def api(base: str, method: str, path: str, body=None, token=None) -> dict:
    payload = _request(base, method, path, body, token)
    if not payload.get("success"):
        raise RuntimeError(f"api failure on {method} {path}: {payload}")
    return payload["data"]


def _ws_url(base: str) -> str:
    return (
        base.replace("https://", "wss://").replace("http://", "ws://")
        + "/ws/competition/"
    )


async def connect(ws_url: str, competition_id: str, identify: dict) -> websockets.WebSocketClientProtocol:
    ws = await websockets.connect(ws_url + competition_id)
    await ws.recv()  # identify_required
    await ws.send(json.dumps(identify))
    return ws


async def expect(ws, event_type: str, window: float, drain: int = 20) -> dict:
    """Read until an event of the given type arrives (auto-pong); None on timeout."""
    for _ in range(drain):
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=window))
        except asyncio.TimeoutError:
            return {}
        if msg.get("type") == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            continue
        if msg.get("type") == event_type:
            return msg
    return {}


async def e2e(base: str, password: str, wait: float) -> None:
    print(f"== E2E contre {base} ==")
    ws_url = _ws_url(base)

    admin_key = api(base, "POST", "/api/admin/key/generate",
                    {"password": password, "label": "e2e"})["access_token"]
    check("cle admin generee", bool(admin_key))

    comp = api(base, "POST", "/api/admin/competitions",
               {"name": "E2E auto-test", "default_points": 10,
                "default_negative_points": -5, "speed_bonus_enabled": True},
               token=admin_key)
    cid = comp["id"]
    check("competition creee", bool(cid), f"code={comp['code']}")

    question = api(base, "POST", f"/api/admin/competitions/{cid}/questions",
                   {"type": "mcq", "text": "E2E: premiere question ?", "position": 1,
                    "duration_seconds": 30, "points": 10, "negative_points": -5},
                   token=admin_key)
    qid = question["id"]
    c1 = api(base, "POST", f"/api/admin/questions/{qid}/choices",
             {"text": "Reponse A", "position": 1, "is_correct": True}, token=admin_key)
    api(base, "POST", f"/api/admin/questions/{qid}/choices",
        {"text": "Reponse B", "position": 2, "is_correct": False}, token=admin_key)
    check("question + choix ajoutes", bool(qid) and bool(c1["id"]))

    p1 = api(base, "POST", "/api/competitions/join",
             {"competition_code": comp["code"], "display_name": "Joueur E2E 1"})
    p2 = api(base, "POST", "/api/competitions/join",
             {"competition_code": comp["code"], "display_name": "Joueur E2E 2"})
    check("deux joueurs ont rejoint", p1["participant_id"] != p2["participant_id"])

    ws_p1 = await connect(ws_url, cid, {"type": "identify", "role": "participant",
                                        "token": p1["access_token"]})
    ws_p2 = await connect(ws_url, cid, {"type": "identify", "role": "participant",
                                        "token": p2["access_token"]})
    ws_admin = await connect(ws_url, cid, {"type": "identify", "role": "admin",
                                           "token": admin_key})
    st_admin = await expect(ws_admin, "competition_state", wait)
    check("ws admin identifie, compteur=2", st_admin.get("participants_connected") == 2,
          str(st_admin)[:160])
    st_p1 = await expect(ws_p1, "competition_state", wait)
    check("ws participant identifie", st_p1.get("participant_id") == p1["participant_id"],
          str(st_p1)[:160])
    await expect(ws_p2, "competition_state", wait)

    api(base, "POST", f"/api/admin/competitions/{cid}/start", token=admin_key)
    ev = await expect(ws_p1, "competition_started", wait)
    check("broadcast competition_started recu par le joueur", bool(ev))

    api(base, "POST", f"/api/admin/competitions/{cid}/questions/{qid}/start", token=admin_key)
    q_p1 = await expect(ws_p1, "question_started", wait)
    q_adm = await expect(ws_admin, "question_started", wait)
    check("broadcast question_started (joueur + admin)", bool(q_p1) and bool(q_adm))

    receipt = api(base, "POST", f"/api/competitions/{cid}/answers",
                  {"question_id": qid, "choice_id": c1["id"]}, token=p1["access_token"])
    # points may exceed the base value thanks to the speed bonus (fast answer).
    check("reponse acceptee + correcte (+bonus)", receipt["is_correct"] is True
          and receipt["points"] >= 10, str(receipt)[:160])
    ev = await expect(ws_admin, "answer_received", wait)
    check("broadcast answer_received (admin)", bool(ev), str(ev)[:160])

    board = api(base, "GET", f"/api/competitions/{cid}/leaderboard", token=p1["access_token"])
    top = board[0] if board else {}
    check("classement: joueur 1 en tete (au moins 10 pts)", top.get("display_name") == "Joueur E2E 1"
          and top.get("score") >= 10, str(board)[:160])

    api(base, "POST", f"/api/admin/competitions/{cid}/finish", token=admin_key)
    ev = await expect(ws_p1, "competition_finished", wait)
    check("broadcast competition_finished (joueur)", bool(ev))
    board_f = api(base, "GET", f"/api/competitions/{cid}/leaderboard", token=p1["access_token"])
    check("classement final accessible", bool(board_f))

    for ws in (ws_p1, ws_p2, ws_admin):
        await ws.close()

    api(base, "DELETE", f"/api/admin/competitions/{cid}", token=admin_key)
    try:
        api(base, "GET", f"/api/admin/competitions/{cid}", token=admin_key)
        gone = False
    except RuntimeError:
        gone = True
    check("competition supprimee (cascade)", gone)

    print("== E2E : TOUT EST OK ==")


def cleanup_all(base: str, password: str) -> None:
    admin_key = api(base, "POST", "/api/admin/key/generate",
                    {"password": password, "label": "cleanup"})["access_token"]
    rows = api(base, "GET", "/api/admin/competitions", token=admin_key)
    print(f"== Nettoyage : {len(rows)} competitions ==")
    for row in rows:
        try:
            if row["status"] in ("running", "paused"):
                try:
                    api(base, "POST", f"/api/admin/competitions/{row['id']}/finish",
                        token=admin_key)
                except RuntimeError:
                    pass
            api(base, "DELETE", f"/api/admin/competitions/{row['id']}", token=admin_key)
            print(f"  supprimee: {row['name']} [{row['code']}]")
        except RuntimeError as exc:
            print(f"  ECHEC {row['code']}: {exc}")
    print("== Nettoyage termine ==")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", nargs="?", default=DEFAULT_BASE)
    parser.add_argument("--password", default="mohamed")
    parser.add_argument("--cleanup-all", action="store_true")
    parser.add_argument("--wait", type=float, default=12.0)
    args = parser.parse_args()
    if args.cleanup_all:
        cleanup_all(args.base, args.password)
        return
    asyncio.run(e2e(args.base, args.password, args.wait))


if __name__ == "__main__":
    main()