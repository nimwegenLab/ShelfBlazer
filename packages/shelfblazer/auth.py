"""Accounts: own users file (outside the data repo), scrypt password hashes, one-time reset links, sign-up passphrase.

Every account has a stable, random user ID (``uid``, e.g. ``U7F3K2QA``). Records and git commits name people only by that ID; names,
usernames and everything else about a person live in this file, outside git. Erasing a person removes their entry and keeps the ID as a
tombstone (never reissued), so the history still reads "U7F3K2QA changed ST-12" but nothing links that ID to anyone any more
(docs/design.md, section 16.2)."""
import contextlib, fcntl, hashlib, hmac, json, os, re, secrets, threading, time, unicodedata

SCRYPT = dict(n=2**15, r=8, p=1, maxmem=96 * 1024 * 1024, dklen=32)
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,31}$")
UID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"     # upper case: a user ID can never be mistaken for a username (always lower case)
UID_RE = re.compile(r"^U[A-Z2-7]{7}$")
UID_IN_TEXT = re.compile(r"(?<!~)\b" + UID_RE.pattern.strip("^$") + r"\b")   # user IDs in commit messages ("ST-12: update by U7F3K2QA"), shown as names
MIN_PASSWORD_LENGTH = 8
RESET_TTL = 24 * 3600
MAX_FAILS, FAIL_WINDOW = 5, 300   # wrong passwords for one username from one address, within the window
MAX_IP_FAILS = 30                 # wrong passwords from one address, all usernames together (stops spraying)
MAX_SIGNUP_FAILS = 5              # wrong sign-up passphrases from one address
MAX_TOKEN_FAILS = 20              # wrong access tokens from one address (their own budget: never locks a valid token or a login)
MAX_NAME_LENGTH = 80
SESSION_TTL = 14 * 24 * 3600      # same as the session cookie
MAX_SESSIONS = 20                 # signed-in browsers per person; the oldest is signed out beyond that
MAX_TOKENS = 5                    # personal access tokens per person (docs/design.md, section 18)
TOKEN_PREFIX = "sbt_"
TOKEN_SCOPES = ("member", "admin")
TOKEN_DEFAULT_DAYS, TOKEN_MAX_DAYS, ADMIN_TOKEN_MAX_DAYS = 90, 365, 30
TOKEN_TOUCH = 600                 # "last used" is written at most this often (seconds)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored) -> bool:
    if not stored:
        return False
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              maxmem=SCRYPT["maxmem"], dklen=len(h) // 2)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


_DUMMY_HASH = []


def _dummy_hash():
    """A hash to check unknown usernames against (made once), so they cost the same time as known ones."""
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(hash_password(secrets.token_hex(8)))
    return _DUMMY_HASH[0]


def clean_name(name):
    """A display name without control characters (tabs, newlines ...) and with single spaces; ValueError when too long."""
    name = "".join(c if unicodedata.category(c)[0] != "C" else " " for c in (name or ""))
    name = " ".join(name.split())
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"name: at most {MAX_NAME_LENGTH} characters")
    return name


def _token_key(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def _sid_key(sid):
    return hashlib.sha256((sid or "").encode()).hexdigest()[:32]


def check_password_strength(pw: str):
    if len(pw) < MIN_PASSWORD_LENGTH:
        return f"use at least {MIN_PASSWORD_LENGTH} characters"
    return None


class Users:
    """JSON file {"users": {name: {...}}, "reset": {sha256(token): {"user", "exp"}}, "signup_passphrase": hash|None}."""

    def __init__(self, path):
        self.path = path
        self._fails = {}  # key -> [timestamps]
        self._fails_lock = threading.Lock()   # hashing runs in worker threads
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._cache = (None, None)            # (file signature, parsed content): pages look people up many times
        if not os.path.exists(path):
            self._write({"users": {}, "reset": {}, "signup_passphrase": None, "tombstones": []})
        if any("uid" not in u for u in self._read()["users"].values()):
            with self._txn() as d:                # upgrade: give every account its user ID once
                for u in d["users"].values():
                    if "uid" not in u:
                        u["uid"] = self._new_uid(d)

    # ---- persistence
    def _load(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def _read(self):
        """The users file, parsed once per change of the file (read only: never modify what this returns)."""
        st = os.stat(self.path)
        sig = (st.st_ino, st.st_mtime_ns, st.st_size)
        if self._cache[0] != sig:
            self._cache = (sig, self._load())
        return self._cache[1]

    def _write(self, data):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @contextlib.contextmanager
    def _txn(self):
        with open(self.path + ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            data = self._load()                   # a private copy: a failed change leaves nothing behind
            data.setdefault("tombstones", [])
            yield data
            self._write(data)

    # ---- queries
    def get(self, username):
        u = self._read()["users"].get((username or "").lower())
        return dict(u, username=username.lower()) if u else None

    def all(self):
        d = self._read()["users"]
        return [dict(v, username=k) for k, v in sorted(d.items())]

    def by_uid(self, uid):
        """The account with this user ID, or None (also for an erased person)."""
        data = self._read()
        index = data.get("_by_uid")
        if index is None:                         # built once per version of the file (held in the cache only, never written)
            index = data["_by_uid"] = {u.get("uid"): k for k, u in data["users"].items()}
        name = index.get(uid)
        return dict(data["users"][name], username=name) if name else None

    def resolve(self, key):
        """A user ID or a username -> the account, or None."""
        key = (key or "").strip()
        return self.by_uid(key) if UID_RE.match(key) else self.get(key)

    def is_erased(self, uid):
        return uid in self._read().get("tombstones", [])

    @staticmethod
    def _new_uid(d):
        taken = {u.get("uid") for u in d["users"].values()} | set(d.get("tombstones", []))
        while True:
            uid = "U" + "".join(secrets.choice(UID_ALPHABET) for _ in range(7))
            if uid not in taken:
                return uid

    def public(self, u):
        return {k: v for k, v in u.items() if k not in ("pw", "sessions", "tokens")}

    def erase(self, username):
        """Remove a person for good (right to erasure): their entry goes, their user ID stays as a tombstone so it is never reissued.
        Returns the user ID. Records keep the ID and show "former member"."""
        username = (username or "").lower()
        with self._txn() as d:
            u = d["users"].pop(username, None)
            if not u:
                raise KeyError(username)
            if not any(x["role"] == "admin" and x["active"] and x["pw"] for x in d["users"].values()):
                raise ValueError("at least one active admin with a password must remain")
            d["tombstones"].append(u["uid"])
            d["reset"] = {k: v for k, v in d["reset"].items() if v["user"] != username}
        with self._fails_lock:
            for key in [k for k in self._fails if k.startswith(username + "|")]:
                del self._fails[key]
        return u["uid"]

    # ---- management
    def create(self, username, name, role="user", password=None, active=True, legacy=None, created=None):
        username = username.lower()
        if not USERNAME_RE.match(username):
            raise ValueError("username: 2-32 chars, lowercase letters, digits, . _ -")
        if role not in ("user", "admin"):
            raise ValueError("bad role")
        with self._txn() as d:
            if username in d["users"]:
                raise ValueError("username already exists")
            d["users"][username] = {"uid": self._new_uid(d), "name": clean_name(name) or username, "role": role, "active": active,
                                    "pw": hash_password(password) if password else None,
                                    "created": created or time.strftime("%Y-%m-%dT%H:%M:%S"), "legacy": legacy or {}}
            return d["users"][username]["uid"]

    def set_created(self, username, created):
        """Set the creation date shown on the Users page (an ISO date-time such as 2014-12-03T14:22:37): the importer knows when the
        old LabCollector account was made. Returns True when it changed."""
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", created or ""):
            raise ValueError("created: use YYYY-MM-DDTHH:MM:SS")
        with self._txn() as d:
            u = d["users"].get(username.lower())
            if not u:
                raise KeyError(username)
            changed = u.get("created") != created
            u["created"] = created
            return changed

    def update(self, username, **kw):
        with self._txn() as d:
            u = d["users"].get(username.lower())
            if not u:
                raise KeyError(username)
            for k, v in kw.items():
                if k not in ("name", "role", "active"):
                    raise ValueError(k)
                if k == "role" and v not in ("user", "admin"):
                    raise ValueError("bad role")
                if k == "name":
                    v = clean_name(v) or username.lower()
                u[k] = v
            if kw.get("active") is False:
                u.pop("tokens", None)                 # a deactivated account keeps no access tokens (an administrator token also stops working
                                                      # when its owner is no longer an administrator: authenticate_token checks)
            # never leave the system without an active admin
            if not any(x["role"] == "admin" and x["active"] and x["pw"] for x in d["users"].values()):
                raise ValueError("at least one active admin with a password must remain")

    def set_collection_visible(self, username, type_slug, visible):
        """The person's "Visible to others" choice for their personal collection of one type (default: visible). Stored with the
        account, outside the data repository, as the list of types they hide ("hidden_collections")."""
        with self._txn() as d:
            u = d["users"].get(username.lower())
            if not u:
                raise KeyError(username)
            hidden = [s for s in u.get("hidden_collections", []) if s != type_slug]
            if not visible:
                hidden.append(type_slug)
            if hidden:
                u["hidden_collections"] = sorted(hidden)
            else:
                u.pop("hidden_collections", None)

    @staticmethod
    def collection_visible(u, type_slug):
        """Whether this account shows its personal collection of the type to others (an unknown account: yes)."""
        return type_slug not in (u or {}).get("hidden_collections", [])

    def set_password(self, username, password):
        """Set a password. Personal access tokens are revoked with it, also when people change their own password: that is what
        they do when they think someone got in, and a token made from a stolen session must not outlive it."""
        err = check_password_strength(password)
        if err:
            raise ValueError(err)
        with self._txn() as d:
            u = d["users"].get(username.lower())
            if not u:
                raise KeyError(username)
            u["pw"] = hash_password(password)
            u.pop("tokens", None)
            d["reset"] = {k: v for k, v in d["reset"].items() if v["user"] != username.lower()}

    def password_version(self, u):
        return hashlib.sha256((u.get("pw") or "").encode()).hexdigest()[:12]

    # ---- reset links
    def make_reset_token(self, username):
        token = secrets.token_urlsafe(24)
        with self._txn() as d:
            if username.lower() not in d["users"]:
                raise KeyError(username)
            d["users"][username.lower()].pop("tokens", None)       # a reset link means the account may be compromised: access tokens go too
            now = time.time()
            d["reset"] = {k: v for k, v in d["reset"].items() if v["exp"] > now and v["user"] != username.lower()}
            d["reset"][hashlib.sha256(token.encode()).hexdigest()] = {"user": username.lower(), "exp": now + RESET_TTL}
        return token

    def reset_user(self, token):
        r = self._read()["reset"].get(hashlib.sha256(token.encode()).hexdigest())
        return r["user"] if r and r["exp"] > time.time() else None

    # ---- sign-up passphrase
    def set_signup_passphrase(self, phrase):
        with self._txn() as d:
            d["signup_passphrase"] = hash_password(phrase) if phrase else None

    def signup_enabled(self):
        return bool(self._read()["signup_passphrase"])

    def check_signup_passphrase(self, phrase):
        stored = self._read()["signup_passphrase"]
        return bool(stored) and verify_password(phrase or "", stored)

    # ---- sessions (server side, so that signing out really ends a session)
    def start_session(self, username):
        """A new session id for this person (stored hashed, with an expiry). Returns the id for the session cookie."""
        sid = secrets.token_urlsafe(24)
        now = time.time()
        with self._txn() as d:
            u = d["users"].get(username.lower())
            if not u:
                raise KeyError(username)
            live = {k: v for k, v in (u.get("sessions") or {}).items() if v > now}
            live[_sid_key(sid)] = now + SESSION_TTL
            u["sessions"] = dict(sorted(live.items(), key=lambda kv: kv[1])[-MAX_SESSIONS:])
        return sid

    def session_valid(self, u, sid):
        exp = (u.get("sessions") or {}).get(_sid_key(sid)) if sid else None
        return bool(exp) and exp > time.time()

    def end_session(self, username, sid):
        with self._txn() as d:
            u = d["users"].get((username or "").lower())
            if u and u.get("sessions"):
                u["sessions"].pop(_sid_key(sid), None)

    # ---- personal access tokens (for the MCP endpoint; stored as SHA-256 hashes, shown once; docs/design.md, section 18)
    def create_token(self, username, name, days=TOKEN_DEFAULT_DAYS, scope="member"):
        """A new token for this person: returns (token text, its record). The text is not stored anywhere, only its hash."""
        name = clean_name(name)
        if not name:
            raise ValueError("give the token a name, for example the computer it is for")
        if scope not in TOKEN_SCOPES:
            raise ValueError("bad scope")
        limit = ADMIN_TOKEN_MAX_DAYS if scope == "admin" else TOKEN_MAX_DAYS
        if not isinstance(days, int) or not 1 <= days <= limit:
            raise ValueError(f"the expiry must be between 1 and {limit} days")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = time.time()
        with self._txn() as d:
            u = d["users"].get((username or "").lower())
            if not u or not u["active"]:
                raise KeyError(username)
            if scope == "admin" and u["role"] != "admin":
                raise ValueError("only administrators can create an administrator token")
            live = {k: v for k, v in (u.get("tokens") or {}).items() if v["expires"] > now}
            if len(live) >= MAX_TOKENS:
                raise ValueError(f"at most {MAX_TOKENS} tokens: revoke one first")
            tid = secrets.token_hex(4)
            live[_token_key(token)] = rec = {"id": tid, "name": name, "created": now, "expires": now + days * 86400, "last_used": None, "scope": scope}
            u["tokens"] = live
        return token, dict(rec)

    def tokens_of(self, username):
        """The person's live tokens (no secrets: not even the hash), newest first."""
        u = self.get(username)
        now = time.time()
        return sorted((dict(t) for t in (u.get("tokens") or {}).values() if t["expires"] > now), key=lambda t: -t["created"]) if u else []

    def revoke_token(self, username, tid):
        with self._txn() as d:
            u = d["users"].get((username or "").lower())
            if not u:
                raise KeyError(username)
            left = {k: v for k, v in (u.get("tokens") or {}).items() if v["id"] != tid}
            found = len(left) != len(u.get("tokens") or {})
            if left:
                u["tokens"] = left
            else:
                u.pop("tokens", None)
            return found

    def revoke_all_tokens(self, username):
        with self._txn() as d:
            u = d["users"].get((username or "").lower())
            if u:
                u.pop("tokens", None)

    def authenticate_token(self, token, ip):
        """(account, token record) for a valid, unexpired token of an active account, else (None, None). A valid token always
        works: a token cannot be guessed (256 random bits), so the throttle only stops a sender that keeps trying wrong ones
        (``token_locked``), and those failures have their own budget, so they never lock anybody's sign-in either."""
        data = self._read()
        index = data.get("_by_token")
        if index is None:                         # built once per version of the file (held in the cache only, never written)
            index = data["_by_token"] = {k: (name, t["id"]) for name, u in data["users"].items() for k, t in (u.get("tokens") or {}).items()}
        hit = index.get(_token_key(token)) if isinstance(token, str) and token.startswith(TOKEN_PREFIX) else None
        u = data["users"].get(hit[0]) if hit else None
        t = next((t for t in (u.get("tokens") or {}).values() if t["id"] == hit[1]), None) if u else None
        if not (u and t and u["active"] and t["expires"] > time.time() and (t["scope"] != "admin" or u["role"] == "admin")):
            with self._fails_lock:
                self._fails.setdefault(f"token|{ip}", []).append(time.time())
            return None, None
        if not t["last_used"] or time.time() - t["last_used"] > TOKEN_TOUCH:
            with self._txn() as d:
                live = (d["users"].get(hit[0]) or {}).get("tokens", {}).get(_token_key(token))
                if live:
                    live["last_used"] = time.time()
        return dict(u, username=hit[0]), dict(t)

    # ---- throttling (in memory): login per username and address, login per address, sign-up passphrase per address
    def _recent(self, key, now):
        fails = [t for t in self._fails.get(key, []) if now - t < FAIL_WINDOW]
        if fails:
            self._fails[key] = fails
        else:
            self._fails.pop(key, None)          # forget quiet keys, so the table does not grow without end
        return len(fails)

    def _key(self, username, ip):
        return f"{(username or '').lower()}|{ip}"

    def is_locked(self, username, ip):
        """Locked when this address failed too often for this username, or for all usernames together.
        Failures from other addresses never lock anyone out, so nobody can lock another person's account."""
        now = time.time()
        with self._fails_lock:
            return self._recent(self._key(username, ip), now) >= MAX_FAILS or self._recent(f"ip|{ip}", now) >= MAX_IP_FAILS

    def record_failure(self, username, ip):
        now = time.time()
        with self._fails_lock:
            for key in (self._key(username, ip), f"ip|{ip}"):
                self._fails.setdefault(key, []).append(now)

    def clear_failures(self, username, ip):
        with self._fails_lock:
            self._fails.pop(self._key(username, ip), None)

    def token_locked(self, ip):
        """Wrong access tokens from this address too often: refuse its next wrong ones with "too many attempts"."""
        with self._fails_lock:
            return self._recent(f"token|{ip}", time.time()) >= MAX_TOKEN_FAILS

    def signup_locked(self, ip):
        with self._fails_lock:
            return self._recent(f"signup|{ip}", time.time()) >= MAX_SIGNUP_FAILS

    def record_signup_failure(self, ip):
        with self._fails_lock:
            self._fails.setdefault(f"signup|{ip}", []).append(time.time())

    def authenticate(self, username, password, ip):
        """Returns (user | None, error message | None). Constant-ish time: always hashes."""
        if self.is_locked(username, ip):
            return None, "Too many attempts. Wait a few minutes."
        u = self.get(username)
        ok = verify_password(password, u["pw"] if u and u["pw"] else _dummy_hash())
        if not (u and ok and u["active"]):
            self.record_failure(username, ip)
            return None, "Wrong username or password."
        self.clear_failures(username, ip)
        return u, None
