"""
verify_emails.py - throw away the dead addresses before you spend a verifier credit on them.

Three levels, cheapest first. Each one only sees what the one before it let through:

  1. syntax   - malformed addresses, role traps, disposable domains, obvious junk. Free, instant.
  2. mx       - does the domain accept mail at all? One DNS lookup per DOMAIN, not per address,
                so a 200,000-row file with 30,000 domains costs 30,000 lookups. Free, fast.
  3. smtp     - ask the mail server whether the mailbox exists, without sending anything.
                Slow, and many servers lie (catch-all) or block you. Off unless you ask for it.

  python verify_emails.py run --in leads.csv [--level mx|smtp] [--par 60] [--limit N]
  python verify_emails.py domains --in leads.csv          just the domain verdicts
  python verify_emails.py status

Writes VERIFY/<name>_verified.csv with a verdict per row, plus _good.csv holding only the
rows worth paying to verify properly. Resumable: domains already checked are never re-checked.

What the verdicts mean:
  good          worth sending to, or worth spending a paid verification on
  catch_all     the server accepts everything, so nobody can tell - treat as risky
  no_mx         the domain cannot receive mail at all. Dead.
  bad_syntax    not an address
  role          info@, sales@ - fine for cold outreach, flagged so you can choose
  disposable    throwaway mailbox provider
  unknown       the check could not finish (timeout, block). Not proof of anything.
"""
import os, re, sys, csv, json, glob, time, socket, smtplib, argparse, datetime
import concurrent.futures as cf

csv.field_size_limit(10 ** 9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("LS_ROOT") or os.path.dirname(HERE))
OUT = os.path.join(ROOT, "VERIFY")
CACHE = os.path.join(OUT, "_domain_cache.json")
EMAIL_RX = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}@([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                      r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)$")
ROLE = {"info", "contact", "kontakt", "office", "sales", "obchod", "predaj", "hello", "ahoj", "mail",
        "admin", "support", "podpora", "help", "firma", "post", "podatelna", "sekretariat", "marketing",
        "accounts", "billing", "fakturacia", "hr", "jobs", "kariera", "praca"}
NEVER = {"noreply", "no-reply", "donotreply", "postmaster", "abuse", "webmaster", "hostmaster",
         "mailer-daemon", "bounce", "spam", "privacy", "dmca", "unsubscribe"}
DISPOSABLE = {"mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com", "temp-mail.org",
              "maildrop.cc", "yopmail.com", "throwawaymail.com", "trashmail.com", "sharklasers.com",
              "getnada.com", "dispostable.com", "mytemp.email", "fakeinbox.com", "mailnesia.com"}
FREE = {"gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "msn.com",
        "aol.com", "icloud.com", "me.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net", "mail.com",
        "zoznam.sk", "azet.sk", "centrum.sk", "post.sk", "seznam.cz", "email.cz", "centrum.cz", "atlas.cz"}
EMAIL_COLS = ("email", "best_email", "e_mail", "email_address", "contact_email", "mail", "emails")


def say(o):
    print(json.dumps(o, ensure_ascii=False, indent=1), flush=True)
    sys.exit(0 if o.get("ok", True) else 1)


def need_dns():
    try:
        import dns.resolver                                      # noqa: F401
    except ImportError:
        say({"ok": False, "error": "dnspython is not installed. Run: pip install dnspython"})


def resolve(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def rel(p):
    try:
        return os.path.relpath(p, ROOT).replace(os.sep, "/")
    except ValueError:
        return p


def load_cache():
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cache(c):
    os.makedirs(OUT, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(c, fh)
    os.replace(tmp, CACHE)


def syntax(email):
    """Free checks. Returns (verdict, domain) - verdict empty means it passed."""
    e = (email or "").strip().lower()
    if not e or " " in e or e.count("@") != 1:
        return "bad_syntax", ""
    m = EMAIL_RX.match(e)
    if not m:
        return "bad_syntax", ""
    user, dom = e.split("@", 1)
    if ".." in e or user.startswith(".") or user.endswith("."):
        return "bad_syntax", dom
    if dom in DISPOSABLE:
        return "disposable", dom
    if user in NEVER:
        return "never_use", dom
    if user in ROLE or any(user.startswith(r + ".") or user.startswith(r + "-") for r in ROLE):
        return "role", dom
    return "", dom


def mx_of(domain):
    """The domain's mail servers, best first. ([], reason) when it cannot receive mail."""
    import dns.resolver
    r = dns.resolver.Resolver()
    r.timeout, r.lifetime = 5, 8
    try:
        ans = r.resolve(domain, "MX")
        hosts = sorted(((x.preference, str(x.exchange).rstrip(".")) for x in ans), key=lambda t: t[0])
        hosts = [h for _, h in hosts if h and h != "."]
        return (hosts, "") if hosts else ([], "null_mx")
    except Exception as e:
        name = type(e).__name__
        if name in ("NXDOMAIN", "NoAnswer"):
            if name == "NoAnswer":
                try:                                   # no MX but an A record still takes mail
                    r.resolve(domain, "A")
                    return [domain], ""
                except Exception:
                    return [], "no_mx"
            return [], "no_domain"
        return [], "dns_" + name.lower()


def smtp_probe(domain, hosts):
    """Ask the server about a real and a nonsense mailbox. If it accepts both, it is a catch-all."""
    if not hosts:
        return "no_mx", ""
    probe = "zz%s@%s" % (int(time.time()) % 100000, domain)
    for host in hosts[:2]:
        try:
            s = smtplib.SMTP(host, 25, timeout=12, local_hostname="localhost")
            s.ehlo_or_helo_if_needed()
            s.mail("verify@" + domain)
            code, _ = s.rcpt(probe)
            try:
                s.quit()
            except Exception:
                pass
            if code in (250, 251):
                return "catch_all", host
            if code in (550, 551, 553, 554, 450, 451):
                return "checkable", host
            return "unknown", host
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, OSError):
            continue
        except Exception:
            continue
    return "blocked", ""


def smtp_one(email, hosts):
    if not hosts:
        return "no_mx"
    dom = email.split("@", 1)[1]
    for host in hosts[:2]:
        try:
            s = smtplib.SMTP(host, 25, timeout=12, local_hostname="localhost")
            s.ehlo_or_helo_if_needed()
            s.mail("verify@" + dom)
            code, _ = s.rcpt(email)
            try:
                s.quit()
            except Exception:
                pass
            if code in (250, 251):
                return "good"
            if code in (550, 551, 553):
                return "no_mailbox"
            return "unknown"
        except Exception:
            continue
    return "unknown"


def read_rows(path, limit=0):
    p = resolve(path)
    if not os.path.exists(p):
        return None, None, "There is no file at %s" % p
    with open(p, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        col = next((c for c in cols if (c or "").strip().lower() in EMAIL_COLS), None)
        if not col:
            return None, None, "No email column in: " + ", ".join([c for c in cols if c][:12])
        rows = []
        for r in rd:
            rows.append(r)
            if limit and len(rows) >= limit:
                break
    return rows, (cols, col), ""


def check_domains(domains, level, par, cache):
    """One lookup per domain, cached between runs. The slow part of the job, done once."""
    todo = [d for d in domains if d and d not in cache]
    done = {"n": 0}
    t0 = time.time()

    def work(d):
        hosts, why = mx_of(d)
        rec = {"mx": hosts[:2], "state": why or "has_mx", "when": datetime.date.today().isoformat()}
        if not why and level == "smtp":
            kind, host = smtp_probe(d, hosts)
            rec["smtp"] = kind
        done["n"] += 1
        n = done["n"]
        if n % 200 == 0 or n == len(todo):
            el = max(1e-6, time.time() - t0)
            print("%d/%d domains checked | %.0f/min | about %.0f min left"
                  % (n, len(todo), n / el * 60, (len(todo) - n) / max(.001, n / el) / 60), flush=True)
        return d, rec

    if todo:
        print("%d domains to check (%d already known), %d at a time"
              % (len(todo), len(domains) - len(todo), par), flush=True)
        with cf.ThreadPoolExecutor(max_workers=par) as ex:
            for d, rec in ex.map(work, todo):
                cache[d] = rec
        save_cache(cache)
    return cache


def step_run(path, level, par, limit, smtp_each):
    need_dns()
    rows, meta, err = read_rows(path, limit)
    if err:
        say({"ok": False, "error": err})
    cols, col = meta
    os.makedirs(OUT, exist_ok=True)
    verdicts, domains = [], set()
    for r in rows:
        v, dom = syntax(r.get(col))
        verdicts.append([v, dom])
        if not v and dom:
            domains.add(dom)
    cache = load_cache()
    if level in ("mx", "smtp"):
        cache = check_domains(sorted(domains), level, par, cache)

    stats = {}
    out_rows = []
    for r, (v, dom) in zip(rows, verdicts):
        rec = cache.get(dom) or {}
        if not v:
            st = rec.get("state", "unchecked")
            if st in ("no_mx", "no_domain", "null_mx"):
                v = "no_mx"
            elif st.startswith("dns_"):
                v = "unknown"
            elif rec.get("smtp") == "catch_all":
                v = "catch_all"
            elif level == "smtp" and smtp_each and rec.get("smtp") == "checkable":
                v = smtp_one(str(r.get(col) or "").strip().lower(), rec.get("mx") or [])
            else:
                v = "good"
        stats[v] = stats.get(v, 0) + 1
        o = dict(r)
        o["verify"] = v
        o["verify_domain_state"] = rec.get("state", "")
        o["verify_smtp"] = rec.get("smtp", "")
        o["verify_free_mailbox"] = "yes" if dom in FREE else ""
        out_rows.append(o)

    stem = os.path.splitext(os.path.basename(resolve(path)))[0][:60]
    newcols = list(cols) + ["verify", "verify_domain_state", "verify_smtp", "verify_free_mailbox"]
    allp = os.path.join(OUT, "%s_verified.csv" % stem)
    goodp = os.path.join(OUT, "%s_good.csv" % stem)
    with open(allp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=newcols, extrasaction="ignore")
        w.writeheader(); w.writerows(out_rows)
    keep = [o for o in out_rows if o["verify"] in ("good", "role")]
    with open(goodp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=newcols, extrasaction="ignore")
        w.writeheader(); w.writerows(keep)
    dead = sum(stats.get(k, 0) for k in ("no_mx", "bad_syntax", "disposable", "never_use", "no_mailbox"))
    return say({"ok": True, "checked": len(out_rows), "domains": len(domains), "level": level,
                "verdicts": dict(sorted(stats.items(), key=lambda kv: -kv[1])),
                "worth_paying_to_verify": len(keep), "thrown_away": dead,
                "saved": "%.0f%% of the file does not need a paid credit" % (100 * dead / max(1, len(out_rows))),
                "all_rows": rel(allp), "good_rows": rel(goodp),
                "next": "Split %s with the file splitter, then send those parts to your verifier." % rel(goodp)})


def step_domains(path, par, limit):
    need_dns()
    rows, meta, err = read_rows(path, limit)
    if err:
        say({"ok": False, "error": err})
    _, col = meta
    doms = sorted({syntax(r.get(col))[1] for r in rows if syntax(r.get(col))[1]})
    cache = check_domains(doms, "mx", par, load_cache())
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "domains_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["domain", "state", "mail_server", "checked"])
        for d in doms:
            r = cache.get(d, {})
            w.writerow([d, r.get("state", ""), (r.get("mx") or [""])[0], r.get("when", "")])
    alive = sum(1 for d in doms if (cache.get(d) or {}).get("state") == "has_mx")
    return say({"ok": True, "domains": len(doms), "can_receive_mail": alive,
                "dead": len(doms) - alive, "file": rel(p)})


def step_status():
    c = load_cache()
    states = {}
    for r in c.values():
        states[r.get("state", "?")] = states.get(r.get("state", "?"), 0) + 1
    files = sorted(glob.glob(os.path.join(OUT, "*_verified.csv")))
    return say({"ok": True, "domains_known": len(c), "by_state": dict(sorted(states.items(), key=lambda kv: -kv[1])),
                "files_verified": len(files), "folder": rel(OUT),
                "note": "Domain results are cached, so a re-run only checks what is new."})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "domains", "status"])
    ap.add_argument("--in", dest="inp", default="")
    ap.add_argument("--level", default="mx", choices=["syntax", "mx", "smtp"])
    ap.add_argument("--par", type=int, default=60)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--smtp-each", action="store_true",
                    help="probe every mailbox, not just the domain. Slow, and it can get your IP blocked.")
    a = ap.parse_args()
    par = max(1, min(200, a.par))
    if a.cmd == "run":
        step_run(a.inp, a.level, par, a.limit, a.smtp_each)
    elif a.cmd == "domains":
        step_domains(a.inp, par, a.limit)
    else:
        step_status()


if __name__ == "__main__":
    main()
