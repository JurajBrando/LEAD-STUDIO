"""
connect.py - the outreach channels of Lead Studio: WhatsApp (OpenWA), social posting (Postiz),
and reading conversations (Agent Reach). Standard library only; every answer is JSON.

  python connect.py whatsapp-check                        is the WhatsApp gateway up and the session logged in
  python connect.py whatsapp-send --to +421900000000 --text "hello"
  python connect.py postiz-channels                       which social accounts are connected
  python connect.py postiz-post --spec post.json          one post, now or scheduled
  python connect.py reach-channels                        channels Agent Reach can read
  python connect.py reach-read --url <conversation url>    read a thread without opening the app

Addresses and keys come from lead_studio_settings.json next to this file (set them on the
Connections page). Nothing here is installed for you: OpenWA, Postiz and Agent Reach each run
as their own program on this computer.

A word of warning that comes from OpenWA itself, not from me: WhatsApp bans numbers that send
first messages to strangers in bulk. This sends ONE message per call, on purpose.
"""
import os, sys, json, time, argparse, subprocess, urllib.parse, urllib.request, urllib.error

APP = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(APP, "lead_studio_settings.json")
DEFAULTS = {"wa_url": "http://localhost:2785", "wa_key": "", "wa_session": "default",
            "postiz_url": "https://api.postiz.com", "postiz_key": "", "reach_cmd": "agent-reach"}


def cfg():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            s.update({k: v for k, v in json.load(fh).items() if k in s and v})
    except (OSError, ValueError):
        pass
    return s


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(0 if obj.get("ok") else 1)


def call(url, method="GET", headers=None, payload=None, timeout=40):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Accept": "application/json"}
    if data:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(raw or "{}"), r.status
            except ValueError:
                return True, {"text": raw[:4000]}, r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1500]
        return False, {"http": e.code, "body": body}, e.code
    except Exception as e:
        return False, {"error": str(e)}, 0


def wa_base(c):
    return c["wa_url"].rstrip("/")


def wa_head(c):
    return {"X-API-Key": c["wa_key"]} if c["wa_key"] else {}


def whatsapp_check():
    c = cfg()
    ok, body, code = call(wa_base(c) + "/api/sessions", headers=wa_head(c), timeout=20)
    if not ok:
        return out({"ok": False, "channel": "whatsapp", "gateway": wa_base(c), "detail": body,
                    "hint": "Start OpenWA on this computer and check the address and API key on the Connections page."})
    sess = body if isinstance(body, list) else body.get("sessions") or body.get("data") or []
    names = [(s.get("name") or s.get("id") or s.get("sessionId") or "?") if isinstance(s, dict) else str(s) for s in sess]
    mine = next((s for s in sess if isinstance(s, dict) and (s.get("name") or s.get("id")) == c["wa_session"]), None)
    return out({"ok": True, "channel": "whatsapp", "gateway": wa_base(c), "sessions": names,
                "using": c["wa_session"], "session_found": bool(mine),
                "status": (mine or {}).get("status") or (mine or {}).get("state") or "",
                "note": "" if mine else "The session '%s' is not in that list - start it in OpenWA or change the name." % c["wa_session"]})


def whatsapp_send(to, text, dry=False):
    c = cfg()
    num = "".join(ch for ch in str(to) if ch.isdigit())
    if len(num) < 8:
        return out({"ok": False, "error": "That does not look like a phone number with a country code: %r" % to})
    if not text.strip():
        return out({"ok": False, "error": "Nothing to send - the message is empty."})
    url = "%s/api/sessions/%s/messages/send-text" % (wa_base(c), urllib.parse.quote(c["wa_session"]))
    body = {"chatId": num + "@c.us", "text": text}
    if dry:
        return out({"ok": True, "dry_run": True, "would_post_to": url, "body": body})
    ok, res, code = call(url, "POST", wa_head(c), body)
    return out({"ok": ok, "channel": "whatsapp", "to": "+" + num, "sent": ok, "http": code, "response": res,
                "chars": len(text)})


def pz(c, path):
    return c["postiz_url"].rstrip("/") + "/public/v1" + path


def pz_head(c):
    return {"Authorization": c["postiz_key"]}        # Postiz wants the bare key, no "Bearer"


def postiz_channels():
    c = cfg()
    if not c["postiz_key"]:
        return out({"ok": False, "error": "No Postiz API key saved. Postiz > Settings > Public API, then paste it on the Connections page."})
    ok, body, code = call(pz(c, "/integrations"), headers=pz_head(c))
    if not ok:
        return out({"ok": False, "channel": "postiz", "api": c["postiz_url"], "detail": body,
                    "hint": "For a self-hosted Postiz the address is your own server, not api.postiz.com."})
    items = body if isinstance(body, list) else body.get("integrations") or body.get("data") or []
    return out({"ok": True, "channel": "postiz", "api": c["postiz_url"],
                "channels": [{"id": i.get("id"), "name": i.get("name"), "platform": i.get("identifier") or i.get("providerIdentifier"),
                              "disabled": bool(i.get("disabled"))} for i in items if isinstance(i, dict)]})


def postiz_post(spec_path, dry=False):
    c = cfg()
    spec = json.load(open(spec_path, encoding="utf-8"))
    content = str(spec.get("content") or "").strip()
    ids = [str(i) for i in (spec.get("channels") or []) if str(i).strip()]
    if not content or not ids:
        return out({"ok": False, "error": "The post needs 'content' and at least one channel id (postiz-channels lists them)."})
    when = str(spec.get("date") or "").strip()
    kind = "now" if (spec.get("now") or not when) else "schedule"
    if kind == "schedule" and "T" not in when:
        return out({"ok": False, "error": "'date' must look like 2026-11-20T09:30:00Z"})
    body = {"type": kind, "date": when or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tags": spec.get("tags") or [], "shortLink": bool(spec.get("short_links")),
            "posts": [{"integration": {"id": i}, "value": [{"content": content, "image": spec.get("images") or []}]} for i in ids]}
    if dry:
        return out({"ok": True, "dry_run": True, "would_post_to": pz(c, "/posts"), "body": body})
    ok, res, code = call(pz(c, "/posts"), "POST", pz_head(c), body, timeout=60)
    return out({"ok": ok, "channel": "postiz", "kind": kind, "when": body["date"], "channels": ids,
                "http": code, "response": res})


def reach(args):
    c = cfg()
    cmd = c["reach_cmd"].split() + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return out({"ok": False, "error": "'%s' is not installed on this computer. Install Agent Reach first "
                                          "(github.com/Panniantong/agent-reach), then try again." % c["reach_cmd"]})
    except subprocess.TimeoutExpired:
        return out({"ok": False, "error": "Agent Reach did not answer within two minutes."})
    text = (p.stdout or p.stderr or "").strip()
    try:
        return out({"ok": p.returncode == 0, "channel": "agent-reach", "data": json.loads(text)})
    except ValueError:
        return out({"ok": p.returncode == 0, "channel": "agent-reach", "text": text[:8000], "code": p.returncode})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["whatsapp-check", "whatsapp-send", "postiz-channels", "postiz-post",
                                    "reach-channels", "reach-read"])
    ap.add_argument("--to"); ap.add_argument("--text"); ap.add_argument("--spec"); ap.add_argument("--url")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "whatsapp-check":
        whatsapp_check()
    elif a.cmd == "whatsapp-send":
        whatsapp_send(a.to or "", a.text or "", a.dry_run)
    elif a.cmd == "postiz-channels":
        postiz_channels()
    elif a.cmd == "postiz-post":
        if not a.spec:
            out({"ok": False, "error": "--spec post.json is required"})
        postiz_post(a.spec, a.dry_run)
    elif a.cmd == "reach-channels":
        reach(["channels"])
    else:
        if not a.url:
            out({"ok": False, "error": "--url is required"})
        reach(["read", a.url])


if __name__ == "__main__":
    main()
