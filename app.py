import argparse
import csv
import difflib
import random
import re
import smtplib
import string
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

import dns.exception
import dns.resolver

SYNTAX = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)

EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "live.com", "msn.com", "comcast.net", "att.net", "me.com",
    "protonmail.com", "sbcglobal.net", "verizon.net", "yahoo.co.uk", "charter.net"
]

FREEMAIL = set(EMAIL_DOMAINS)

TOP_LEVEL_DOMAINS = {"con": "com", "cmo": "com", "vom": "com", "ocm": "com",
             "cim": "com", "comm": "com", "nett": "net", "ner": "net",
             "orgg": "org"}


def clean(e):
    e = (e or "").strip().lower().replace("mailto:", "")
    e = re.sub(r"^[\s<>\"';,]+|[\s<>\"';,]+$", "", e)
    return e.replace(" ", "").replace(",", ".")


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def fix_domain(d):
    d = re.sub(r"\.+", ".", d.strip("."))
    if "." in d:
        base, tld = d.rsplit(".", 1)
        d = f"{base}.{TOP_LEVEL_DOMAINS.get(tld, tld)}"
    if d not in EMAIL_DOMAINS:
        close = difflib.get_close_matches(d, EMAIL_DOMAINS, n=1, cutoff=0.88)
        if close:
            d = close[0]
    return d


def candidates(first, last):
    f, l = norm_name(first), norm_name(last)
    if not f or not l:
        return []
    pats = [f"{f}.{l}", f"{f}{l}", f"{f[0]}{l}", f"{f[0]}.{l}", f,
            f"{f}_{l}", f"{f}{l[0]}", f"{l}.{f}", f"{l}{f}", l,
            f"{l}{f[0]}", f"{f}-{l}"]
    seen, out = set(), []
    for p in pats:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


class Domain:
    def __init__(self, name):
        self.name = name
        self.mx = None # None = not loaded yet
        self.catch_all = None # True / False / None(unknown)
        self.load_lock = threading.Lock()
        self.smtp_lock = threading.Lock()

    def load(self):
        with self.load_lock:
            if self.mx is not None:
                return
            hosts = []
            try:
                ans = dns.resolver.resolve(self.name, "MX", lifetime=8)
                hosts = [str(r.exchange).rstrip(".")
                         for r in sorted(ans, key=lambda r: r.preference)]
                hosts = [h for h in hosts if h]  # null MX ('.') -> empty
            except dns.resolver.NoAnswer:
                try:
                    dns.resolver.resolve(self.name, "A", lifetime=8)
                    hosts = [self.name]
                except dns.exception.DNSException:
                    pass
            except dns.exception.DNSException:
                pass
            self.mx = hosts


_domains, _dlock = {}, threading.Lock()


def get_domain(name):
    with _dlock:
        d = _domains.setdefault(name, Domain(name))
    d.load()
    return d


def probe(email, mx_hosts, args):
    """Return SMTP RCPT code, or None if the server couldn't be reached."""
    for host in mx_hosts[:2]:
        try:
            s = smtplib.SMTP(local_hostname=args.helo, timeout=args.timeout)
            s.connect(host, 25)
            s.ehlo_or_helo_if_needed()
            s.mail(args.mail_from)
            code, _ = s.rcpt(email)
            try:
                s.quit()
            except Exception:
                pass
            return code
        except (smtplib.SMTPException, OSError):
            continue
    return None


def check_mailbox(d, email, args):
    """-> 'valid' | 'invalid' | 'catch_all' | 'unknown'. Call under d.smtp_lock."""
    if d.catch_all is None:
        time.sleep(args.delay)
        rand = "".join(random.choices(string.ascii_lowercase, k=14))
        c = probe(f"{rand}@{d.name}", d.mx, args)
        if c is None or 400 <= c < 500:
            return "unknown"
        d.catch_all = 200 <= c < 300
    time.sleep(args.delay)
    code = probe(email, d.mx, args)
    if code is None or 400 <= code < 500:
        return "unknown"
    if d.catch_all:
        return "catch_all"
    return "valid" if 200 <= code < 300 else "invalid"


def process(row, a):
    out = dict(row)
    email = clean(row.get(a.email_col, ""))
    out.update(status="", checked_email=email, suggested_email="", notes="")
    notes = []

    local, domain = (email.split("@") + [""])[:2] if email.count("@") == 1 else ("", "")
    if not domain and a.domain_col:
        domain = clean(row.get(a.domain_col, ""))
    if not domain:
        out["status"] = "invalid_syntax"
        return out
    if not SYNTAX.match(email):
        notes.append("bad syntax")

    # resolve domain (typo fix only if the original doesn't have a mail server)
    d = get_domain(domain)
    if not d.mx:
        fixed = fix_domain(domain)
        d2 = get_domain(fixed) if fixed != domain else None
        if d2 and d2.mx:
            notes.append(f"domain typo {domain} -> {fixed}")
            d = d2
        else:
            out["status"] = "domain_has_no_mail_server"
            out["notes"] = "; ".join(notes)
            return out
    changed_domain = d.name != domain

    if not local and a.first_col:
        local = ""  # nothing to verify
    email = f"{local}@{d.name}" if local else ""
    out["checked_email"] = email

    if not a.smtp or d.name in FREEMAIL:
        out["status"] = "mx_ok_mailbox_unverified"
        if changed_domain:
            out["suggested_email"] = email
        if d.name in FREEMAIL:
            notes.append("freemail provider: mailbox can't be reliably verified")
        out["notes"] = "; ".join(notes)
        return out

    with d.smtp_lock:
        state = check_mailbox(d, email, a) if email else "invalid"
        if state == "valid":
            out["status"] = "valid"
            if changed_domain:
                out["suggested_email"] = email
        elif state == "catch_all":
            out["status"] = "risky_catch_all"
            notes.append("domain accepts any address; can't confirm mailbox")
            if changed_domain:
                out["suggested_email"] = email
        elif state == "unknown":
            out["status"] = "unknown"
            notes.append("server unreachable, greylisted, or port 25 blocked")
        else:  # invalid -> look for alternatives
            out["status"] = "undeliverable"
            if d.catch_all is False:
                found = []
                for cand in candidates(row.get(a.first_col, ""), row.get(a.last_col, ""))[: a.max_candidates]:
                    addr = f"{cand}@{d.name}"
                    if addr == email:
                        continue
                    if check_mailbox(d, addr, a) == "valid":
                        found.append(addr)
                        break
                if found:
                    out["status"] = "found_alternative"
                    out["suggested_email"] = found[0]
                else:
                    notes.append("no pattern match found")
            else:
                notes.append("no names/pattern search possible")
    out["notes"] = "; ".join(notes)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", nargs="?", default="bounced.csv",
                   help="CSV of bounced emails (default: bounced.csv)")
    p.add_argument("output", nargs="?", default="email-errors.csv",
                   help="results file (default: email-errors.csv)")
    p.add_argument("--email-col", default="email")
    p.add_argument("--first-col", default="first_name")
    p.add_argument("--last-col", default="last_name")
    p.add_argument("--domain-col", default="")
    p.add_argument("--smtp", action="store_true", help="enable SMTP mailbox probing (needs outbound port 25)")
    p.add_argument("--helo", default="localhost", help="your real mail host's FQDN")
    p.add_argument("--mail-from", default="verify@example.com", help="a real address you control")
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--delay", type=float, default=1.0, help="seconds between probes to the same domain")
    p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--max-candidates", type=int, default=12)
    a = p.parse_args()

    if a.input.lower() == a.output.lower():
        sys.exit("Input and output files are the same; the input would be overwritten. "
                 "Pick a different output name.")

    with open(a.input, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("Input is empty.")

    extra = ["status", "checked_email", "suggested_email", "notes"]
    fields = list(rows[0].keys()) + [c for c in extra if c not in rows[0]]

    results = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(process, r, a): i for i, r in enumerate(rows)}
        for n, fut in enumerate(as_completed(futs), 1):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:  # never lose a row
                results[i] = {**rows[i], "status": "error", "notes": str(e)}
            if n % 50 == 0:
                print(f"{n}/{len(rows)} done", file=sys.stderr)

    with open(a.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    from collections import Counter
    print(dict(Counter(r["status"] for r in results)), file=sys.stderr)


if __name__ == "__main__":
    main()